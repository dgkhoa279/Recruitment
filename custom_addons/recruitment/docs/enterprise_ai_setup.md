# MuK AI setup cho Recruitment

## Cấu hình

1. Run Odoo and PostgreSQL as usual.
2. Trong MuK AI, cấu hình provider, API key và model.
3. Gán model mong muốn cho một MuK AI Agent.
4. Trong Recruitment Settings, chọn Agent đó tại trường `MuK AI Agent`.
5. Khi đổi model trên Agent, lần quét CV và sinh JD tiếp theo dùng model mới ngay.

## Security properties

- Recruitment contains no standalone chatbox, agent loop, or tool registry.
- MuK AI owns conversations and `muk_ai_tool_builder` owns generated tools.
- Generated tools execute through the restricted context and the current user's Odoo rules.
- Recruitment không giữ API key và không gọi trực tiếp Gemini/Ollama.
- MuK AI là nơi duy nhất resolve provider/model và thực hiện request.
- Chatter của lần quét thành công ghi rõ Agent, provider và model đã dùng.

## RAG lifecycle

- MuK AI hiện chưa có API embedding chung giữa các provider.
- Vì vậy cron RAG vector và semantic ranking được tạm ngưng thay vì tiếp tục gọi
  adapter Ollama cũ.
- Quét CV bằng AI và fallback đối sánh kỹ năng cục bộ vẫn hoạt động độc lập.

Scanned image-only CVs require a separate local OCR service before they can be
indexed in fully offline mode.
