# openwebui-pipeline

Service FastAPI qui présente une API OpenAI-compatible à Open WebUI, proxifie les requêtes vers les vrais providers LLM (Anthropic / OpenAI), et logge chaque échange dans `memory-api`.

## Architecture

```
Open WebUI ──/v1/chat/completions──▶ openwebui-pipeline ──▶ Anthropic/OpenAI APIs
                                              │
                                              └─POST /v1/messages──▶ memory-api
```

## Configuration côté Open WebUI

```
OPENAI_API_BASE_URL=http://openwebui-pipeline:9099
OPENAI_API_KEY=<même valeur que PIPELINE_API_KEY>
```

Open WebUI traite ce service comme un endpoint OpenAI custom — il appelle `/v1/models` au boot pour son dropdown puis `/v1/chat/completions` à chaque message.

## Modèles supportés (Phase 2)

- `claude-opus-4-7` → Anthropic claude-opus-4-7
- `claude-sonnet-4-6` → Anthropic claude-sonnet-4-6
- `claude-haiku-4-5` → Anthropic claude-haiku-4-5-20251001
- `gpt-4o` → OpenAI gpt-4o
- `gpt-4o-mini` → OpenAI gpt-4o-mini

Ajouter dans `MODEL_MAP` (app/main.py) pour de nouveaux modèles.

## Best-effort logging

Si memory-api est down, l'échec POST est loggé en WARNING mais NE BLOQUE PAS la réponse retournée à Open WebUI. L'UX utilisateur prime sur la mémoire (qui sera retraitable depuis Open WebUI Mongo data si besoin Phase 2).
