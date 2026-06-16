import re

# TAXONOMY DICTIONARIES
_CATEGORIES = {
    "Web/Frontend Frameworks": [
        "react", "vue", "angular", "svelte", "nextjs", "nuxt", "frontend", "ui", "component",
        "tailwind", "css", "html", "browser", "dom"
    ],
    "Backend / APIs": [
        "express", "django", "flask", "fastapi", "spring", "asp.net", "rails", "backend",
        "rest", "graphql", "api", "microservice", "server", "middleware", "rpc", "grpc"
    ],
    "Data Engineering & AI/ML Pipelines": [
        "tensorflow", "pytorch", "keras", "scikit-learn", "pandas", "numpy", "spark", "hadoop",
        "airflow", "kafka", "machine learning", "deep learning", "neural network", "data pipeline",
        "etl", "llm", "rag", "vector database", "model"
    ],
    "DevOps, Infrastructure & Security": [
        "docker", "kubernetes", "terraform", "ansible", "jenkins", "github actions", "ci/cd",
        "aws", "gcp", "azure", "cloud", "infrastructure", "security", "authentication", "oauth",
        "deployment", "container"
    ],
    "Databases & Storage": [
        "mysql", "postgresql", "mongodb", "redis", "cassandra", "elasticsearch", "database",
        "sql", "nosql", "orm", "storage", "cache"
    ],
    "Mobile & Cross-Platform Applications": [
        "react native", "flutter", "swift", "kotlin", "android", "ios", "mobile", "app",
        "cross-platform", "xamarin", "ionic"
    ],
    "Systems & Embedded Programming": [
        "rust", "c++", "c", "go", "assembly", "embedded", "os", "kernel", "system",
        "performance", "memory management", "compiler", "webassembly", "wasm"
    ]
}

def classify_category(repo: dict, tags: list[str]) -> str:
    """Executes taxonomy alignment to categorize the payload target."""
    paragraphs = repo.get("extracted_paragraphs", [])
    desc_text = " ".join(paragraphs)
    
    # Pre-process raw description to disambiguate specific terms
    # Prevent "ReAct" (Reasoning and Acting) from being categorized as React (Frontend UI)
    # We match "ReAct" case-sensitively to distinguish it from React/react
    desc_text = re.sub(r'\bReAct\b', 'reasoning_and_acting', desc_text)
    raw_desc = desc_text.lower()
    
    # Also handle tags case-sensitively
    processed_tags = []
    for t in tags:
        if t == "ReAct":
            processed_tags.append("reasoning_and_acting")
        else:
            processed_tags.append(t)
            
    tag_set = set(t.lower() for t in processed_tags)
    scores = {cat: 0 for cat in _CATEGORIES}
    
    for cat, keywords in _CATEGORIES.items():
        for kw in keywords:
            if kw in tag_set:
                scores[cat] += 3
            if kw in raw_desc:
                scores[cat] += 1
                
    if max(scores.values(), default=0) == 0:
        return "General / Other"
        
    return max(scores, key=scores.get)

