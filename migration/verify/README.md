# chat-client migration verifiers

Run from project root:

```bash
bash migration/verify/verify-chat-client-CONFIG.sh
bash migration/verify/verify-chat-client-LOGGING.sh
bash migration/verify/verify-chat-client-API-KIT.sh
bash migration/verify/verify-chat-client-IDAM.sh
bash migration/verify/verify-chat-client-DB.sh
bash migration/verify/verify-chat-client-LLM.sh
```

Migration completeness table:

| Adopted package | Verifier script |
|---|---|
| `cloud_dog_config` | `verify-chat-client-CONFIG.sh` |
| `cloud_dog_logging` | `verify-chat-client-LOGGING.sh` |
| `cloud_dog_api_kit` | `verify-chat-client-API-KIT.sh` |
| `cloud_dog_idam` | `verify-chat-client-IDAM.sh` |
| `cloud_dog_db` | `verify-chat-client-DB.sh` |
| `cloud_dog_llm` | `verify-chat-client-LLM.sh` |
