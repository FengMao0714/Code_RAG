---
name: retrieval-evaluation
description: Use when designing, implementing, or reviewing retrieval ranking, reranking, answer quality, citations, benchmark datasets, golden questions, or regression tests for Code_RAG.
---

# Retrieval Evaluation

Do not judge retrieval quality by a single impressive demo. Build a small repeatable evaluation set before broadening the system.

Golden query design:

- Include symbol lookup questions: "Where is X defined?"
- Include behavior questions: "How does feature Y decide Z?"
- Include cross-file flow questions: "What calls this API and how is the result used?"
- Include configuration questions.
- Include negative questions where the repo does not contain enough evidence.
- Include ambiguity questions where multiple files look similar.

Metrics to track:

- Recall@k for expected files and chunks.
- MRR or nDCG for ranking quality.
- Citation coverage: every answer claim should map to retrieved evidence.
- Faithfulness: answer must not introduce facts absent from retrieved context.
- Latency by stage: filtering, parsing, embedding, vector search, rerank, synthesis.
- Cost by stage when external APIs are used.

Review retrieval changes by comparing before and after on the same snapshot and query set. When a change improves one query but harms another, preserve the tradeoff in a short note or test comment.

Answer rules:

- Cite files and line ranges whenever available.
- Say when evidence is missing or weak.
- Distinguish retrieved facts from inference.
- Prefer concise, grounded answers over broad speculation.
