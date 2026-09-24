# -*- coding: utf-8 -*-
import logging
from markupsafe import Markup
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class VnpayController(http.Controller):

    @http.route('/payment/vnpay/return', type='http', auth='public', website=True, csrf=False)
    def vnpay_return(self, **kw):
        """
        Handles Return URL redirect from VNPAY Payment Gateway.
        """
        params = dict(kw)
        vnp_secure_hash = params.get('vnp_SecureHash')
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

        # Fetch VNPAY provider to verify signature via Single Source of Truth
        provider = request.env['payment.provider'].sudo().search([('code', '=', 'vnpay')], limit=1)
        is_valid_signature = provider.vnpay_verify_signature(params, vnp_secure_hash) if provider else False

        if response_code == '00' and is_valid_signature:
            # THANH TOÁN THÀNH CÔNG -> CẬP NHẬT TRẠNG THÁI ỨNG VIÊN & TẠO INVOICE
            application.action_confirm_payment_and_hire(amount=amount_vnp, method='vnpay')
            redirect_url = f"/web#id={application.id}&model=recruitment.application&view_type=form"
            return request.redirect(redirect_url)
        else:
            # THANH TOÁN THẤT BẠI HOẶC SAIC HỮ KÝ
            fail_reason = "Sai chữ ký bảo mật" if not is_valid_signature else f"Mã lỗi VNPAY: {response_code}"
            msg = f"❌ Thanh toán VNPAY Thất bại! Nguyên nhân: {fail_reason}"
            application.message_post(body=Markup(f"<div class='alert alert-danger'>{msg}</div>"))
            redirect_url = f"/web#id={application.id}&model=recruitment.application&view_type=form"
            return request.redirect(redirect_url)
