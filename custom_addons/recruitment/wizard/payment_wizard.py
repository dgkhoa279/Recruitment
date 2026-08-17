import datetime
import hashlib
import hmac
import urllib.parse
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PaymentWizard(models.TransientModel):
    _name = "recruitment.payment.wizard"
    _description = "Wizard Thanh toán Đơn ứng tuyển"

    application_id = fields.Many2one(
        "recruitment.application",
        string="Đơn ứng tuyển",
        required=True,
        ondelete="cascade",
    )

    candidate_name = fields.Char(
        related="application_id.candidate_id.name",
        string="Tên Ứng viên",
        readonly=True,
    )

    job_position_name = fields.Char(
        related="application_id.job_position_id.name",
        string="Vị trí tuyển dụng",
        readonly=True,
    )

    amount = fields.Float(
        string="Số tiền thanh toán (VNĐ)",
        default=2000000.0,
        required=True,
    )

    provider_id = fields.Many2one(
        "payment.provider",
        string="Phương thức thanh toán",
        domain="[('state', 'in', ['enabled', 'test'])]",
        required=True,
    )

    def action_confirm_payment(self):
        self.ensure_one()
        if not self.application_id:
            raise ValidationError("Không tìm thấy đơn ứng tuyển để thanh toán!")

        provider = self.provider_id
        method_code = provider.code or "vietqr"

        app_id = self.application_id.id
        amount_int = int(self.amount)
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        ref_code = f"APP{app_id}_{now_str}"

        # 1. Xây dựng URL chuyển hướng đến Cổng Thanh toán chuẩn VNPAY (HMAC-SHA512)
        if method_code == "vnpay":
            tmn_code = (provider.vnpay_tmn_code or "7IDZYOQT").strip()
            secret_key = (provider.vnpay_hash_secret or "99999999").strip()

            vnp_params = {
                "vnp_Version": "2.1.0",
                "vnp_Command": "pay",
                "vnp_TmnCode": tmn_code,
                "vnp_Amount": amount_int * 100,  # VNPAY tính đơn vị Đồng * 100
                "vnp_CurrCode": "VND",
                "vnp_TxnRef": ref_code,
                "vnp_OrderInfo": f"Thanh toan don ung tuyen APP {app_id}",  # Tiếng Việt không dấu & không ký tự đặc biệt
                "vnp_OrderType": "other",
                "vnp_Locale": "vn",
                "vnp_ReturnUrl": "http://localhost:8069/payment/vnpay/return",
                "vnp_IpAddr": "127.0.0.1",
                "vnp_CreateDate": now_str,
            }

            # Sắp xếp tham số theo alphabet key theo đúng quy định VNPAY
            sorted_params = sorted(vnp_params.items())
            hash_data = urllib.parse.urlencode(sorted_params)

            # Tính toán mã kiểm tra bảo mật vnp_SecureHash (HMAC-SHA512)
            secure_hash = hmac.new(
                secret_key.encode("utf-8"),
                hash_data.encode("utf-8"),
                hashlib.sha512
            ).hexdigest()

            checkout_url = f"https://sandbox.vnpayment.vn/paymentv2/vpcpay.html?{hash_data}&vnp_SecureHash={secure_hash}"

        elif method_code == "momo":
            momo_code = (provider.momo_partner_code or "MOMO").strip()
            checkout_url = f"https://test-payment.momo.vn/v2/gateway/api/create?partnerCode={momo_code}&orderId={ref_code}&amount={amount_int}"

        elif method_code == "zalopay":
            zalopay_id = (provider.zalopay_app_id or "2553").strip()
            checkout_url = f"https://sb-openapi.zalopay.vn/v2/create?appid={zalopay_id}&amount={amount_int}&apptransid={ref_code}"

        else:  # vietqr (Ngân hàng chuyển khoản trực tiếp)
            # Đối với VietQR, xác nhận chuyển khoản ngân hàng ngay
            self.application_id.action_confirm_payment_and_hire(
                amount=self.amount,
                method=method_code,
            )
            checkout_url = f"https://img.vietqr.io/image/MB-0348786481-compact2.png?amount={amount_int}&addInfo=APP{app_id}%20FASTTRACK&accountName=RECRUITMENT%20VIP"

        # 2. Trả về action chuyển hướng trình duyệt sang trang thanh toán
        return {
            "type": "ir.actions.act_url",
            "url": checkout_url,
            "target": "new",
        }
