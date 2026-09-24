# Enterprise AI setup (50–100 CV)

## Recommended local deployment

1. Run Odoo and PostgreSQL as usual.
2. Enable pgvector as the PostgreSQL administrator:

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

3. Run Ollama as a separate service and install one chat model plus one embedding model:

   ```bash
   ollama pull qwen3:1.7b
   ollama pull embeddinggemma
   ```

   Model weights are managed by Ollama and are not loaded inside the Odoo
   worker process or committed under `custom_addons/recruitment/models`.

4. In Recruitment Settings configure:

   - Ollama Endpoint: `http://localhost:11434`
   - Ollama Chat Model: the installed chat model
   - Ollama Embedding Model: `embeddinggemma`
   - Embedding Dimensions: match the selected embedding model
   - Gemini Fallback: enable only when cloud fallback is acceptable
   - Gemini API Key and Model: required only when fallback is enabled

5. Upgrade the `recruitment` module after enabling pgvector so the optional
   `embedding_vector` column is created.

## Security properties

- Recruitment contains no standalone chatbox, agent loop, or tool registry.
- MuK AI owns conversations and `muk_ai_tool_builder` owns generated tools.
- Generated tools execute through the restricted context and the current user's Odoo rules.
- Ollama is always attempted first.
- Gemini is called only after an Ollama `ProviderError`, when fallback is enabled
  and an API key is configured.
- Disable Gemini Fallback to guarantee generation remains local.
- Embeddings never fall back to Gemini, preventing incompatible vector spaces.

## RAG lifecycle

- A CV is indexed after scanning and a JD is indexed after create/update.
- Chunks are 250 words with a 40-word overlap.
- Index and query must use the same provider, model, and vector dimensions.
- PostgreSQL pgvector is used when installed; JSON cosine remains a portable
  fallback for development and small datasets.
- Retrieval obeys the current user's chunk record rules and returns three
  citation-bearing chunks to the agent.

Scanned image-only CVs require a separate local OCR service before they can be
indexed in fully offline mode.
