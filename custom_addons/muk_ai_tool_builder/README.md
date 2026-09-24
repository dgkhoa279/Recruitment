# MuK AI Tool Builder — workflow 17.0.5.2

## Áp dụng code

Khởi động lại Odoo với code mới và **Upgrade `muk_ai_tool_builder` cùng
`muk_ai_ollama` (nếu đang dùng Ollama)** trên database
cần dùng (có thêm model/field/view/ACL). Không chỉ refresh trình duyệt. Không cần
cài thêm Python package; bộ chạy riêng dùng thư viện chuẩn Python trên Linux.
Code chưa được kiểm thử hay áp dụng vào database trong lần triển khai này.

## Truyền model đã xác định sang bước tạo tool (17.0.6.4)

- Dùng lại các model đã inspect thành công ở lượt hiện tại, theo builder skill.
  Nếu chỉ có một model, schema khai báo nhiệm vụ không yêu cầu AI nhập lại model;
  backend tự gắn model đó vào contract trước khi kiểm tra quyền và field.
- Provider cũ có thể gửi model trùng với model đã inspect. Nếu gửi tên khác,
  backend trả lỗi kèm model đúng và yêu cầu sửa contract tại cùng bước, không
  chuyển sang tìm model khác hoặc yêu cầu user cung cấp tên field.
- Nếu có nhiều model đã inspect, schema chỉ cho chọn trong tập đó. Backend vẫn
  kiểm tra model theo skill, quyền thao tác và quyền field như trước.
- Source phải dùng đúng tập model của contract đã lưu. Sai model sẽ nhận thông
  báo rõ tập model đúng để sửa source, không thay contract theo source sai.
- Không chạy kiểm thử, gọi AI, hoặc cập nhật database trong lần thay đổi này.

## Dừng routing khi trùng tên tool (17.0.6.3)

Session 32 không lặp do sai model: backend đã phát hiện chính xác
`count_job_positions` tồn tại và trả `can_repair=true`. Tuy nhiên routing chưa
được đánh dấu kết thúc nên schema của vòng sau vẫn chỉ mở
`route_tool_request`, làm Ollama gọi lại cùng yêu cầu 20 lần.

Xung đột tên nay là kết quả terminal, không retry. Backend không tạo hoặc ghi
đè dữ liệu, đóng toàn bộ tool schema của lượt đó và yêu cầu model trả một thông
báo ngắn: user gửi yêu cầu mới để repair tool đang có, hoặc dùng tên mới. Nếu
model không trả nội dung thì session báo đúng lỗi xung đột thay vì chạy vòng lặp.

## Chặn vòng lặp authoring và bắt buộc context (17.0.6.2)

- Xác định nguyên nhân session 31: Ollama gửi cùng một Odoo module source có
  `from odoo import ...` và `class models.Model` tới `propose_generated_tool`
  16 lần. Sandbox cần script body chỉ dùng `ctx`/`params`, nên backend từ chối
  `ImportFrom`; lifecycle cũ vẫn mở lại cùng bước đến giới hạn vòng lặp.
- Prompt authoring nay mô tả rõ source là script body, cấm import/class/def và
  đưa ví dụ `ctx.search_count`. Lỗi backend gần nhất được đưa lại có cấu trúc;
  payload retry bắt buộc phải thay đổi.
- Backend đếm lỗi theo bước và fingerprint tool/arguments/error. Hai payload
  lỗi giống hệt, hoặc ba lỗi trong cùng bước, làm lượt tạo dừng an toàn; không
  còn gọi lặp đến `Maximum iterations reached`. Discovery lặp mới hỏi user;
  lỗi sinh source lặp sẽ dừng và báo đúng lỗi, không bắt user sửa code hộ AI.
- Router lấy cả tên tool của yêu cầu create. Backend kiểm tra xung đột tên trước
  khi phân tích/lưu source và buộc tên proposal khớp tên đã kiểm tra.
- Nếu không có màn hình nghiệp vụ được ghim, create chỉ được hỏi user mở đúng
  màn hình và tạo chat mới; không còn tự tìm tất cả model trong Odoo.
- Controller lưu màn hình nghiệp vụ gần nhất ngay cả khi chưa có floating chat,
  giữ cả `action_id`. Backend dùng action/menu để thu hẹp thêm theo ứng dụng
  hiện tại, bên cạnh module, role tham khảo và live ACL của user.

Chưa chạy kiểm thử hay nâng cấp database theo yêu cầu của người dùng.

## Luồng mới

1. Chat → model gọi `route_tool_request` để chọn create/repair/run/chat.
   Luồng create tiếp tục `search_authorized_models` → `inspect_authorized_model`.
2. `define_generated_tool_task`: khai báo summary, target, expected_result,
   uses_ai và từng model với operations/read_fields/write_fields. Backend lưu
   cả yêu cầu nguyên văn từ conversation, không lấy từ lời AI tự khai.
3. `propose_generated_tool`: phân tích AST, đối chiếu đúng model/thao tác/field
   với task contract và kiểm tra quyền. Lưu script/revision rồi xác thực tĩnh.
4. `Active` chỉ có nghĩa xác thực tĩnh thành công, **không chứng nhận đúng ý định**.
5. Generated Tools → Run: duyệt nhiệm vụ, nhập tham số từng lần chạy.
6. Preview: Python chạy trong process riêng, đọc qua API Odoo theo quyền user,
   ghi/xóa/tạo được mô phỏng thành danh sách thay đổi. Không gọi các mutation
   hooks thật. Sinh bản nháp AI chỉ được gọi khi user đồng ý rõ ràng.
7. Confirm: kiểm tra user, quyền, revision, input, company scope, hạn 10 phút;
   khóa các bản ghi đích, đối chiếu lại dữ liệu đọc và snapshots trước khi ghi.
   Áp dụng **đúng kế hoạch do script tạo trong Preview**, không chạy lại LLM và
   không sinh nội dung mới sau khi user duyệt. Double-click được serialize.
8. Lưu lịch sử chạy và kết quả. “Report Wrong Result” lưu phản hồi nghiệp vụ;
   user yêu cầu AI sửa, AI inspect lỗi/source rồi tạo revision mới.

Lệnh gọi tool trong chat chỉ tạo link tới form review; không tự thực thi dữ liệu.
Chế độ approval `off` của chat cũng không bỏ qua form này. Các phương thức
thực thi nội bộ là private; các nút xác nhận chặn cả context của MCP lẫn marker
ContextVar của luồng AI, không cho AI tự xác nhận bằng `call_method`.

## Sửa context rỗng và schema Ollama (17.0.6.1)

- `captureViewContext` lưu màn hình nghiệp vụ gần nhất vào `sessionStorage` ngay
  cả khi chưa mở floating chat. Khi tạo chat trong ứng dụng MuK AI và controller
  hiện tại không phải model nghiệp vụ, `seedSessionContext` dùng lại context đó.
  Không lưu model hệ thống `ir.*`, `muk_ai.*`, `muk_mcp.*`; dữ liệu chỉ tồn tại
  trong tab trình duyệt. Context vẫn hiển thị để user có thể unpin.
- Toàn bộ từng bước lifecycle của Ollama dùng structured JSON rồi mới chuyển qua
  dispatcher/audit. `define_generated_tool_task` nhận đúng object `contract`;
  backend đồng thời chấp nhận an toàn dạng năm field phẳng để tương thích phiên
  hoặc provider cũ, nhưng vẫn từ chối field lạ và vẫn validate toàn bộ contract.
- Mỗi vòng chỉ mở bước tiếp theo do backend tính và công cụ hỏi user. Khi tool
  Active đã được lưu, lifecycle đóng và model chỉ được trả tóm tắt, tránh tạo
  lặp revision. Lỗi retryable có thể chuyển bước về discovery; trong context
  đã khóa phạm vi thì thiếu model dẫn đến hỏi user, không tìm toàn hệ thống.
- Với tool chỉ đếm: `uses_ai=false`, `read_fields=[]`; `total` và `model` là key
  kết quả JSON, không phải field Odoo. Backend vẫn là nguồn quyết định cuối cùng.

Phiên 30 được đọc ở chế độ read-only để chẩn đoán: `view_context=null` và Ollama
đã gửi năm field contract ở cấp ngoài, gây `unexpected keyword argument` bốn lần.
Không sửa dữ liệu phiên này. Chưa chạy kiểm thử.

Cần restart Odoo, upgrade `muk_ai`, `muk_ai_ollama`, `muk_ai_tool_builder`, hard
refresh trình duyệt, vào một màn hình Recruitment rồi tạo chat mới. Phiên cũ
không tự có lại context đã thiếu.

## Context-first theo module và quyền (17.0.6.0)

Khi route xác định yêu cầu `create`, backend đọc `view_context` đã được MuK AI
ghim từ màn hình Odoo. Từ model hiện tại, backend xác định module định nghĩa,
các model có menu nhìn thấy trong cùng module, role/group thuộc module và quyền
CRUD hiệu lực của user dưới từng builder skill. Chỉ ứng viên vượt qua live ACL
và skill policy được truyền cho model. Model hiện tại đứng đầu danh sách nhưng
không mặc nhiên là mục tiêu nếu câu user nhắc một đối tượng khác trong module.

Nếu context hợp lệ, lifecycle đi thẳng đến inspect một model trong phạm vi đó;
`search_authorized_models` không xuất hiện nên model không thể tìm toàn hệ thống.
Nếu không có pinned business context, luồng discovery cũ mới là fallback. Nếu
context có nhưng user không có model hợp lệ, hệ thống hỏi rõ thay vì tự mở rộng.
Field access được kiểm tra khi inspect/contract; record rules áp dụng trên bản ghi
thật ở Preview/Run. Tên role chỉ là ngữ cảnh giải thích, không thay thế quyết định
quyền của backend.

Khi fallback buộc phải hỏi vì hai model cùng nhãn, câu trả lời chứa chính xác một
tên model đã cung cấp được lưu là lựa chọn của người dùng. Inspect sau đó không
đòi model sinh thêm `selection_reason`, khắc phục vòng hỏi lặp trước đây. Đây là
đối chiếu tên kỹ thuật trong tập ứng viên, không phải regex phân loại tiếng Việt.

Chưa kiểm thử. Restart Odoo, upgrade `muk_ai_tool_builder`, ghim/mở chat từ màn
hình nghiệp vụ liên quan và bắt đầu chat mới để metadata mỗi lượt được tạo lại.

## Ràng buộc tham số discovery (17.0.5.5)

- Schema của `inspect_authorized_model.model` chỉ cho phép tên kỹ thuật đã
  được discovery theo quyền user ở lượt hiện tại. Không chấp nhận `query`
  thay cho `model`, hoặc tên tự đoán chưa được Odoo trả về.
- Riêng các bước tìm/inspect model của Ollama dùng JSON Schema (`format`)
  để chọn một tool và tham số hợp lệ thay vì dựa vào native tool calling.
  Adapter kiểm tra cấu trúc/tên model, rồi đi qua dispatcher/audit/quyền như
  trước; mỗi quyết định vẫn chỉ là một vòng LLM, không retry ngầm. Các bước
  khai báo nhiệm vụ và sinh script tiếp tục dùng cơ chế tool calling hiện có.
- Sau hai lần lỗi cùng lời gọi discovery, hoặc bốn lỗi discovery trong lượt,
  chỉ còn công cụ hỏi user; không tiếp tục lặp truy vấn sai. Trả lời làm rõ của
  user mở lại discovery, nhưng không gỡ trạng thái từ chối quyền.
- Câu hỏi ở bước này là chọn ứng dụng/model (`resolution=text`), không phải
  popup xin quyền đọc metadata. Giữ câu trả lời của user từ cả user entry và
  kết quả `ask_user` trong ngữ cảnh gửi vòng tiếp theo.

Không chứng minh model đã hiểu đúng nghiệp vụ; tên hợp lệ vẫn có thể là model
khác ứng dụng. Việc đối chiếu module/menu, hỏi rõ và duyệt nhiệm vụ vẫn cần thiết.
Chưa kiểm thử. Restart Odoo, upgrade cả `muk_ai_tool_builder` và `muk_ai_ollama`,
mở chat mới để thử; không tự thay đổi database hoặc cấu hình tài nguyên Ollama.

## Phân biệt model cùng nhãn và giữ yêu cầu (17.0.5.4)

- Discovery tìm theo nhãn/tên kỹ thuật ở ngôn ngữ hiện tại và tiếng Anh. Khi
  tìm bằng tên kỹ thuật, vẫn mở rộng sang các model cùng nhãn tiếng Anh để
  không che mất lựa chọn khác. Không hardcode model/module Recruitment.
- Chỉ sau khi qua ACL/policy mới trả metadata: module định nghĩa/mở rộng,
  menu/action và ứng dụng theo cây menu **user được phép thấy**, tối đa 5 đường
  dẫn/model. Không trả domain/context của action hoặc dữ liệu nghiệp vụ.
  Danh sách module không đồng nghĩa ứng dụng sở hữu; cần đối chiếu menu.
- Kết quả có `same_label_models` và cờ `truncated`. Nếu có model cùng nhãn,
  inspect yêu cầu `selection_reason` dựa trên yêu cầu user và module/menu;
  prompt yêu cầu `ask_user` nếu chưa đủ ngữ cảnh phân biệt. Lý do do model
  cung cấp được ghi nhận, **không phải chứng minh ngữ nghĩa đã đúng**. Backend
  không tự chọn một model vì nó đứng đầu hoặc vì user có quyền trên model đó.
- Mỗi vòng authoring gửi lại nguyên yêu cầu gốc, câu trả lời làm rõ, ứng viên
  đã inspect và bước còn thiếu ở cuối input gửi provider. Không thêm user turn
  giả vào DB; không nâng nội dung user/tool lên system prompt. Giữ nguyên dấu.
  Không tự tăng context/RAM Ollama hoặc thêm lời gọi LLM để retry.

Chưa chạy kiểm thử. Restart Odoo và upgrade `muk_ai_tool_builder`, sau đó mở
chat mới. Với yêu cầu chỉ ghi “Job Position” mà không chỉ rõ ứng dụng, việc AI
hỏi chọn giữa các menu/model cùng tên là hành vi mong đợi, không phải lỗi.

## Kiểm soát thứ tự tạo tool (17.0.5.3)

Catalog và dispatcher chỉ mở bước tiếp theo khi đã hoàn thành điều kiện trước:
discovery → inspect metadata → define task → propose/revise source. Backend lưu
bằng chứng inspect theo lượt, skill, model và quyền trong JSON discoveries hiện
có; kiểm tra từng model/quyền của contract, không chỉ dựa vào lời model khẳng định.
Luồng repair phải đọc source của tool đã chọn trước khi đi tiếp. Có thể quay lại
discovery/inspect để bổ sung model, không mất các kết quả đã tìm trong cùng lượt.

Prompt yêu cầu tìm bằng tên nghiệp vụ và đối chiếu nhãn model với yêu cầu, không
đoán tiền tố module. Không hardcode model Recruitment hoặc dùng regex ngữ nghĩa.
Điều này không bảo đảm model luôn chọn đúng; user vẫn phải duyệt nhiệm vụ.

Lỗi lifecycle được ghi nhận là thất bại và giữ nguyên chi tiết lỗi gần nhất.
Nếu model dừng trước khi lưu revision Active, giao diện hiển thị bước còn thiếu
và lỗi đó thay vì chỉ “AI returned no output”. Không tự chạy script hoặc retry
LLM ngầm. Thay đổi này chưa được kiểm thử; restart Odoo, upgrade
`muk_ai_tool_builder`, mở chat mới để chủ hệ thống tự kiểm thử.

## Hiểu ý định bằng model (17.0.5.2)

Đã bỏ toàn bộ regex phân loại câu và bước bỏ dấu trong `models/session.py`.
Lời user được giữ nguyên và đưa cho model đang cấu hình trên agent (Ollama khi
chọn model local). Không thêm provider. Với Ollama/Google, vòng đầu tiên dành
cho định tuyến dùng prompt ngắn và JSON Schema bắt buộc. Model trả intent,
summary, tool_name, question; backend kiểm tra cấu trúc rồi chuyển thành lời gọi
`route_tool_request` hoặc `ask_user` qua dispatcher hiện có. Không phụ thuộc model
tự phát ra native tool call ở bước đầu. Các vòng nghiệp vụ sau vẫn dùng tool calling.
Đây là một vòng model trong cùng chat loop, vẫn được tính token/chi phí và giới hạn
iteration. Provider khác tiếp tục dùng router qua native tool calling như trước.

Adapter Ollama gửi `format` với JSON Schema; riêng schema `muk_tool_intent` có
`think=false`, temperature=0 để ưu tiên trả JSON ngắn. Giữ giới hạn output-token
đã cấu hình trên provider; không thay đổi chế độ sinh chat/script thông thường.
Tham chiếu API: https://docs.ollama.com/api/chat . JSON trung gian không hiển thị
thành câu trả lời cho user; giao diện vẫn ghi nhận bước route/ask và usage thực tế.

Khi skill tạo tool khả dụng, đầu mỗi user request chỉ mở `route_tool_request`
và công cụ hỏi lại. Sau khi định tuyến:

- create: mở workflow tạo script; không mở công cụ đọc/ghi nghiệp vụ trực tiếp.
- repair: mở workflow sửa, giới hạn vào tool đã chọn.
- run: mở tool đã chọn để tạo form Preview/Confirm.
- chat: dùng công cụ chat/nghiệp vụ thông thường theo bộ lọc agent hiện có.

Backend lưu intent/summary/tool_name trên user entry bằng metadata nội bộ;
provider không nhận các marker nội bộ này. Mỗi user entry mới cần định tuyến
lại. Các phiên đang chạy có marker cũ vẫn tiếp tục theo trạng thái đã lưu.
Một yêu cầu đã được chọn luồng không được model đổi luồng giữa chừng để bỏ qua
kiểm tra; khi cần thay đổi ý định, user gửi yêu cầu mới. ACL và chính sách
readonly vẫn là quyết định của backend, không phụ thuộc lời model khẳng định.

Model vẫn có thể chọn sai intent. JSON sai/rỗng được phản hồi qua router để model
thử lại một lần, dưới giới hạn cost/iteration hiện tại; sai liên tiếp hai lần thì
dừng với lỗi định dạng rõ ràng. Regenerate bắt đầu lại hạn mức thử của turn mới.
Mỗi chunk vẫn kiểm tra hủy phiên và heartbeat; JSON trung gian không được dùng
làm nội dung trả lời. Nếu model chọn sai `chat`, chất lượng lựa chọn cần cải thiện
bằng model/prompt, không có regex dự phòng đoán lại ngữ nghĩa.

Tra cứu `ir.model` vẫn dùng tên/nhãn model; đã bỏ cách xếp hạng dựa trên từ của
prompt bị bỏ dấu. Model chọn ứng viên từ metadata và quyền trả về thực tế.
Regex kiểm tra định dạng tên tool trong `generated_tool.py` được giữ nguyên.

## Quyền và giới hạn

- Model nghiệp vụ vẫn được xét ACL động, quyền field và record rules theo user.
  Skill chỉ thu hẹp quyền. Agent readonly áp dụng cả chat và Run trực tiếp.
- Không cho script truy cập transient model hoặc control plane `ir.*`,
  `muk_ai.*`, `muk_mcp.*`: tránh tự duyệt, sửa policy hoặc tạo automation để
  vượt rào. Đây là rào hệ thống, không phải policy riêng cho từng role Recruitment.
- `ctx.unlink` không còn bị yêu cầu `values` như create/write.
- Read với `fields=[]` chỉ trả ID, không vô tình trả mọi field của model.
- Source chạy bằng Python `-I -S`, không nhận environment/credentials của Odoo.
  API RPC chỉ trao đổi JSON, không truyền ORM object. Worker giới hạn RAM
  256 MiB, CPU/wall-time theo tool, 20.000 bước và 2.000 API call.
- Tối đa 2 execution transaction đồng thời mỗi database; SQL statement timeout
  bị thu hẹp theo tool, không nới rộng timeout đã được quản trị cấu hình.
- **Chưa phải sandbox container/OS đầy đủ.** Audit hook và AST là defense in
  depth. ORM/provider vẫn chạy trong Odoo; thời gian chờ network và tác động
  của custom hooks không có cơ chế cưỡng chế hủy tổng thể bằng worker Python.
  Trước khi chạy mã không tin cậy ở quy mô production cần OS isolation/RPC
  service riêng và kiểm toán các business hook được phép gọi.

## Giới hạn Preview cần biết

- Phải đọc hết dữ liệu cần thiết trước khi thay đổi cùng model. Không hỗ trợ
  đọc lại model đã có mutation trong cùng script.
- Mỗi bản ghi chỉ có một mutation trong kế hoạch; gộp các field thành một write.
- Create độc lập được hỗ trợ, trả ID âm tạm trong Preview. Không dùng ID đó để
  đọc/ghi/xóa tiếp trong cùng tool. ID thật xuất hiện trong `created_records`.
- Không hỗ trợ one2many/many2many command writes vì chúng có thể thay đổi model
  ngoài manifest. Hãy thao tác model liên quan một cách tường minh.
- Preview kiểm tra khả năng/record rules của existing records. Record rules
  của create và constraints/inverse/business validations thực tế còn phải qua
  ORM khi Confirm; Preview không bảo đảm bước ghi thật sẽ thành công.
- AI drafts có thể gửi dữ liệu tới provider cấu hình và tiêu thụ tài nguyên.
  Confirm tái sử dụng draft. Hủy Preview không hoàn tác chi phí đã phát sinh.
- Confirm có savepoint cho thay đổi DB nhưng **không thể rollback email/webhook
  đã gửi hoặc tác động bên ngoài từ hooks**. Không tự retry mutation lỗi.
- Dữ liệu đọc được đối chiếu lại; các bản ghi đích được khóa khi áp dụng. Đây
  không phải khóa toàn bộ kết quả tìm kiếm hoặc một snapshot toàn hệ thống.
- Task contract do AI đề xuất có thể vẫn hiểu sai. Kiểm tra AST độc lập phát
  hiện lệch model/thao tác/field/AI usage; user duyệt yêu cầu và kết quả Preview.
  Chưa có bộ chấm ngữ nghĩa tự động chứng minh chất lượng nội dung sinh ra.
- Revision limit mặc định vẫn là 3 tổng cộng. Bản sửa không qua xác thực giữ
  định nghĩa đang dùng (trừ lỗi quyền thì vô hiệu hóa); số revision đã thử vẫn tăng.
- Audit lưu tên tham số và hash thay vì nguyên giá trị đầu vào. Form transient
  chứa tham số và Preview, tự dọn theo cơ chế Odoo; lịch sử kết quả/lỗi vẫn
  có thể chứa dữ liệu nghiệp vụ và chỉ mở cho owner/admin theo ACL.
- JSON Schema hỗ trợ kiểu đơn, object/array lồng tối đa 6 cấp, required, enum,
  minimum/maximum, giới hạn độ dài/số phần tử, date/date-time. Constraint khác
  bị từ chối, không âm thầm bỏ qua. Input cấp cao nhất phải có tên khai báo.

## Bộ tình huống để chủ hệ thống tự đánh giá

Chưa chạy các tình huống này; đây là tiêu chí nghiệm thu thủ công.

| Yêu cầu/thao tác | Kết quả mong đợi |
| --- | --- |
| Tạo tool đếm Job Position | Đúng model được discovery, contract chỉ đọc, Run nhập được tham số |
| Viết mô tả Job nhưng source chỉ đếm | Bị từ chối nếu không khớp contract ghi/sinh AI; user thấy nguyên yêu cầu để phát hiện contract sai |
| Interviewer đếm Interview | Chỉ đếm bản ghi được record rules cho thấy |
| Interviewer thao tác model không có ACL | Từ chối, không đổi model hoặc xin quyền thay user |
| Thu hồi quyền sau khi Preview | Confirm từ chối trước khi thay đổi dữ liệu |
| Tool xóa dữ liệu | Không lỗi values ở AST; Preview chưa xóa; Confirm mới xóa theo ACL/rules |
| Thiếu/sai kiểu/ngoài giới hạn tham số | Backend từ chối trước chạy |
| Preview tool ghi dữ liệu | DB chưa có mutation; danh sách before/values hiển thị |
| Dữ liệu đích/input/revision đổi | Preview cũ không được áp dụng |
| Bấm Confirm hai lần | Chỉ áp dụng một lần |
| AI gọi trực tiếp nút Confirm | Từ chối kể cả khi cố xóa context session |
| Không đồng ý gọi AI | Preview cần AI bị chặn; không tự fallback gọi provider |
| Confirm draft đã sinh | Không gọi LLM thêm lần nữa |
| Script cấp phát quá mức/vòng lặp dài | Worker bị giới hạn; không chạy script trong Odoo process |
| Tool chạy được nhưng sai ý | Report Wrong Result → AI inspect phản hồi → revision mới |

Các test cũ trong `tests/` thuộc workflow declarative trước đây và chưa được
chuyển sang workflow review/plan; không dùng chúng làm bằng chứng bản này đạt.
