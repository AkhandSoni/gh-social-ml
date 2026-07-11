import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from database.connector import PostgreSQLConnector

logger = logging.getLogger("pipeline.feedback.storage")


def _normalize_user_uuid(user_id: str) -> str:
    try:
        return str(uuid.UUID(str(user_id)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid user_id UUID: {user_id}") from exc


@dataclass(frozen=True)
class FeedbackRecord:
    user_id: str
    repo_id: str
    interaction_type: str
    feedback_score: float
    updated_at: datetime | str | None = None


class FeedbackStore:
    """Persistent effective feedback state.

    Each row represents one current state bit for a user/repository/action. This
    allows a repository to be liked and saved at the same time while keeping
    each state transition idempotent.
    """

    def __init__(self, db_connector: PostgreSQLConnector | None = None) -> None:
        self.db = db_connector or PostgreSQLConnector()

    def init_schema(self) -> None:
        if not self.db.enabled:
            return
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_feedback (
                user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                repo_id UUID NOT NULL REFERENCES repo(repo_id) ON DELETE CASCADE,
                interaction_type VARCHAR(50) NOT NULL,
                feedback_score DOUBLE PRECISION NOT NULL
                    CHECK (feedback_score >= -1.0 AND feedback_score <= 1.0),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, repo_id, interaction_type)
            );
            CREATE INDEX IF NOT EXISTS user_feedback_user_updated_idx
                ON user_feedback (user_id, updated_at DESC);
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'user_feedback'::regclass
                      AND conname = 'user_feedback_pkey'
                      AND (
                          SELECT array_agg(a.attname ORDER BY k.ordinality)
                          FROM unnest(conkey) WITH ORDINALITY AS k(attnum, ordinality)
                          JOIN pg_attribute a
                            ON a.attrelid = conrelid
                           AND a.attnum = k.attnum
                      ) IS DISTINCT FROM ARRAY['user_id', 'repo_id', 'interaction_type']
                ) THEN
                    ALTER TABLE user_feedback DROP CONSTRAINT user_feedback_pkey;
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'user_feedback'::regclass
                      AND conname = 'user_feedback_pkey'
                ) THEN
                    ALTER TABLE user_feedback
                    ADD CONSTRAINT user_feedback_pkey
                    PRIMARY KEY (user_id, repo_id, interaction_type);
                END IF;
            END $$;
            """
        )
        conn.commit()

    def record(
        self,
        user_id: str,
        repo_id: str,
        interaction_type: str,
        feedback_score: float,
    ) -> FeedbackRecord | None:
        if not -1.0 <= feedback_score <= 1.0:
            raise ValueError("feedback_score must be in the range [-1.0, 1.0]")
        user_id = _normalize_user_uuid(user_id)
        if not self.db.enabled:
            return None

        self.init_schema()
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO user_feedback (
                user_id, repo_id, interaction_type, feedback_score, updated_at
            )
            SELECT %s::uuid, repo_id, %s, %s, CURRENT_TIMESTAMP
            FROM repo
            WHERE repo_id::text = %s OR full_name = %s
            ON CONFLICT (user_id, repo_id, interaction_type) DO UPDATE SET
                feedback_score = EXCLUDED.feedback_score,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_feedback.feedback_score IS DISTINCT FROM EXCLUDED.feedback_score
            RETURNING user_id::text, repo_id::text, interaction_type,
                      feedback_score, updated_at;
            """,
            (user_id, interaction_type, feedback_score, repo_id, repo_id),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                """
                SELECT 1 FROM repo
                WHERE repo_id::text = %s OR full_name = %s
                LIMIT 1;
                """,
                (repo_id, repo_id),
            )
            if not cursor.fetchone():
                conn.commit()
                raise ValueError(f"Repository not found: {repo_id}")
            conn.commit()
            return None
        conn.commit()
        if not isinstance(row, (tuple, list)):
            # Some unit-test database doubles do not model RETURNING rows.
            return FeedbackRecord(user_id, repo_id, interaction_type, feedback_score)
        return FeedbackRecord(*row)

    def delete(
        self,
        user_id: str,
        repo_id: str,
        *,
        interaction_type: str | None = None,
    ) -> bool:
        user_id = _normalize_user_uuid(user_id)
        if not self.db.enabled:
            return False
        self.init_schema()
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM user_feedback
            WHERE user_id = %s::uuid
              AND repo_id IN (
                  SELECT repo_id FROM repo
                  WHERE repo_id::text = %s OR full_name = %s
              )
              AND (%s IS NULL OR interaction_type = %s);
            """,
            (user_id, repo_id, repo_id, interaction_type, interaction_type),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

    def list_for_user(self, user_id: str) -> list[FeedbackRecord]:
        user_id = _normalize_user_uuid(user_id)
        if not self.db.enabled:
            return []
        self.init_schema()
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT f.user_id::text, r.full_name, f.interaction_type,
                   f.feedback_score, f.updated_at
            FROM user_feedback f
            JOIN repo r ON r.repo_id = f.repo_id
            WHERE f.user_id = %s::uuid
            ORDER BY f.updated_at DESC;
            """,
            (user_id,),
        )
        return [FeedbackRecord(*row) for row in cursor.fetchall()]

    def scores_for_user(self, user_id: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for record in self.list_for_user(user_id):
            scores[record.repo_id] = max(
                -1.0,
                min(1.0, scores.get(record.repo_id, 0.0) + record.feedback_score),
            )
        return scores


def apply_feedback_scores(
    candidates: list[dict[str, Any]],
    feedback_by_repo: dict[str, float],
    *,
    max_adjustment: float = 2.5,
    dislike_filter_threshold: float = -0.9,
) -> list[dict[str, Any]]:
    """Apply effective feedback without promoting already-consumed exact repos.

    Explicit dislikes remove exact matches. Positive feedback is treated as a
    consumed/seen signal here; it should inform candidate generation for similar
    unseen repositories, not boost the same repository back into the feed.
    """
    adjusted: list[dict[str, Any]] = []
    for candidate in candidates:
        identity = str(candidate.get("full_name") or candidate.get("repo_id") or "")
        score = feedback_by_repo.get(identity, 0.0)
        if score <= dislike_filter_threshold:
            continue
        if score > 0.0:
            continue
        item = dict(candidate)
        adjustment = max(-max_adjustment, min(max_adjustment, score * max_adjustment))
        item["feedback_score"] = score
        item["feedback_adjustment"] = adjustment
        item["final_score"] = float(item.get("final_score") or 0.0) + adjustment
        adjusted.append(item)
    adjusted.sort(key=lambda item: item["final_score"], reverse=True)
    return adjusted
