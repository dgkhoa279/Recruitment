# Kiến trúc AI hiện hành

## Trách nhiệm từng file

| Thành phần | Trách nhiệm |
|---|---|
| `models/ai_provider.py` | Adapter Ollama chính và Gemini fallback cho phân tích, sinh nội dung và embedding. Không quản lý hội thoại. |
| `models/ai_service.py` | Dịch vụ AI nghiệp vụ cho phân tích CV, sinh mô tả JD, lập chỉ mục và RAG retrieval. |
| `models/cv_chunk.py` | Lưu CV/JD chunks, metadata embedding, pgvector và cron re-index. |
| `models/application.py` | Điểm vào nghiệp vụ quét CV và trạng thái lập chỉ mục của hồ sơ. |
| `models/job_position.py` | Trạng thái lập chỉ mục JD và hành động semantic ranking ứng viên. |
| `models/res_config_settings.py` | Cấu hình Ollama local, embeddings và Gemini fallback. |
| `data/ai_cron_data.xml` | Re-index CV/JD theo batch nhỏ. |
| `muk_ai`, `muk_ai_ollama`, `muk_ai_tool_builder` | Hội thoại, agent, gọi model và vòng đời tạo/chạy/sửa tool. |

## Luồng runtime

```text
CV/JD -> extract text -> chunks -> embeddings -> PostgreSQL/pgvector
Người dùng -> MuK AI chat -> Ollama/Gemini -> MuK tools -> Odoo ACL/record rules
```

## Ranh giới kiến trúc

- Module Recruitment không chứa chatbox, agent loop hoặc tool registry riêng.
- Không load GGUF trực tiếp trong Odoo worker.
- Ollama quản lý model weights ở ngoài source addon.
- Embedding luôn chạy qua Ollama; Gemini chỉ fallback cho tác vụ generation của Recruitment.
- Gemini fallback phải được bật rõ ràng và có API key hợp lệ.
- Chat và generated tools chỉ đi qua MuK AI để tránh hai luồng hội thoại song song.
- Quyền chạy generated tool do `muk_ai_tool_builder` kiểm tra lại bằng ACL và record rules.
