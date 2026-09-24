# Kiến trúc AI hiện hành

## Trách nhiệm từng file

| Thành phần | Trách nhiệm |
|---|---|
| `muk_ai.provider` / `muk_ai.agent` | Quản lý tập trung API key, provider và model. |
| `models/ai_service.py` | Dịch vụ nghiệp vụ gọi MuK AI để phân tích CV và sinh mô tả JD. |
| `models/cv_chunk.py` | Lưu CV/JD chunks, metadata embedding, pgvector và cron re-index. |
| `models/application.py` | Điểm vào nghiệp vụ quét CV và trạng thái lập chỉ mục của hồ sơ. |
| `models/job_position.py` | Trạng thái lập chỉ mục JD và hành động semantic ranking ứng viên. |
| `models/res_config_settings.py` | Chọn MuK AI Agent cho Recruitment; không lưu API key riêng. |
| `data/ai_cron_data.xml` | Cơ chế RAG cũ được giữ ở trạng thái tạm ngưng cho đến khi MuK AI có API embedding chung. |
| `muk_ai`, `muk_ai_ollama`, `muk_ai_tool_builder` | Hội thoại, agent, gọi model và vòng đời tạo/chạy/sửa tool. |

## Luồng runtime

```text
CV/JD -> extract text -> Recruitment AI service -> MuK AI Agent -> provider/model đã chọn
Người dùng -> MuK AI chat -> provider/model -> MuK tools -> Odoo ACL/record rules
```

## Ranh giới kiến trúc

- Module Recruitment không chứa chatbox, agent loop hoặc tool registry riêng.
- Không load GGUF trực tiếp trong Odoo worker.
- Recruitment không gọi trực tiếp Ollama hoặc Gemini và không lưu API key riêng.
- Model của Agent được chọn trong MuK AI là model thực tế dùng để quét CV và sinh JD.
- RAG vector tạm ngưng vì MuK AI chưa có giao diện provider-agnostic cho embedding.
- Chat và generated tools chỉ đi qua MuK AI để tránh hai luồng hội thoại song song.
- Quyền chạy generated tool do `muk_ai_tool_builder` kiểm tra lại bằng ACL và record rules.
