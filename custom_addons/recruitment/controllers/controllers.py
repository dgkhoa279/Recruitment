# -*- coding: utf-8 -*-
import hashlib
import hmac
import urllib.parse
from markupsafe import Markup
from odoo import http
from odoo.http import request


class RecruitmentPaymentController(http.Controller):

    @http.route('/payment/vnpay/return', type='http', auth='public', website=True, csrf=False)
    def vnpay_return(self, **kw):
        params = dict(kw)
        vnp_secure_hash = params.pop('vnp_SecureHash', None)
        params.pop('vnp_SecureHashType', None)

        response_code = params.get('vnp_ResponseCode')
        txn_ref = params.get('vnp_TxnRef', '')
        amount_vnp = float(params.get('vnp_Amount', 0)) / 100.0 if params.get('vnp_Amount') else 0.0

        # Parse application_id from TxnRef (Format: APP155_20260817144400)
        app_id = None
        if txn_ref and 'APP' in txn_ref:
            try:
                raw_app = txn_ref.split('_')[0]
                app_id = int(raw_app.replace('APP', ''))
            except Exception:
                pass

        if not app_id:
            return "Không tìm thấy thông tin Đơn ứng tuyển hợp lệ!"

        application = request.env['recruitment.application'].sudo().browse(app_id)
        if not application.exists():
            return "Đơn ứng tuyển không tồn tại trong hệ thống!"

        # Lấy Secret Key cấu hình từ Payment Provider VNPAY
        provider = request.env['payment.provider'].sudo().search([('code', '=', 'vnpay')], limit=1)
        secret_key = (provider.vnpay_hash_secret or '').strip() if provider else ''

        # Kiểm tra chữ ký bảo mật HMAC-SHA512 checksum từ VNPAY
        is_valid_signature = True
        if secret_key and vnp_secure_hash:
            sorted_params = sorted([(k, v) for k, v in params.items() if v is not None])
            hash_data = urllib.parse.urlencode(sorted_params)
            calculated_hash = hmac.new(
                secret_key.encode('utf-8'),
                hash_data.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            if calculated_hash.lower() != vnp_secure_hash.lower():
                is_valid_signature = False

        if response_code == '00' and is_valid_signature:
            # THANH TOÁN THÀNH CÔNG -> CẬP NHẬT HIRED
            application.action_confirm_payment_and_hire(amount=amount_vnp, method='vnpay')
            redirect_url = f"/web#id={application.id}&model=recruitment.application&view_type=form"
            return request.redirect(redirect_url)
        else:
            # THANH TOÁN THẤT BẠI HOẶC BỊ HỦY -> KHÔNG ĐỔI STAGE
            msg = f"❌ Thanh toán VNPAY Thất bại! Mã lỗi: {response_code}"
            application.message_post(body=Markup(f"<div class='alert alert-danger'>{msg}</div>"))
            redirect_url = f"/web#id={application.id}&model=recruitment.application&view_type=form"
            return request.redirect(redirect_url)


class AICopilotController(http.Controller):

    @http.route('/recruitment/ai_copilot/chat', type='json', auth='user', csrf=True)
    def ai_copilot_chat(self, message, history=None, **kw):
        if not message:
            return {"error": "Tin nhắn rỗng"}
        res = request.env['recruitment.ai.service'].sudo().process_chat_message(message, history=history)
        return res

    @http.route('/recruitment/ai_copilot/execute_tool', type='json', auth='user', csrf=True)
    def ai_copilot_execute_tool(self, tool_id, **kw):
        tool = request.env['recruitment.ai.tool'].sudo().browse(int(tool_id))
        if not tool.exists():
            return {"error": "Công cụ không tồn tại"}
        try:
            res = tool.action_execute_tool()
            return {"status": "success", "result": res}
        except Exception as e:
            return {"error": str(e)}
