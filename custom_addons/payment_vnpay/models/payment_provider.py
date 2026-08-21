# -*- coding: utf-8 -*-
import datetime
import hashlib
import hmac
import urllib.parse
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PaymentProviderVnpay(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("vnpay", "VNPAY")],
        ondelete={"vnpay": "set default"},
    )

    vnpay_tmn_code = fields.Char(
        string="VNPAY TMN Code",
        help="Mã Website (TMN Code) do VNPAY cấp",
    )

    vnpay_hash_secret = fields.Char(
        string="VNPAY Hash Secret",
        help="Chuỗi secret key cấu hình VNPAY",
    )

    def _get_default_payment_method_codes(self):
        """ Override of payment to return default payment method codes for VNPAY. """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'vnpay':
            return default_codes
        return ['vnpay']

    def _vnpay_get_api_url(self):
        """Returns the VNPAY Gateway endpoint URL based on environment state."""
        self.ensure_one()
        return "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"

    def vnpay_generate_checkout_url(self, amount, reference, order_info=None, return_url=None):
        """
        SINGLE SOURCE OF TRUTH: Builds signed VNPAY Checkout URL matching official VNPAY Python SDK get_payment_url().
        """
        self.ensure_one()
        if not self.vnpay_tmn_code or not self.vnpay_hash_secret:
            raise ValidationError("Chưa cấu hình VNPAY TMN Code hoặc Secret Key! Vui lòng nhập thông số cấu hình trong Cổng Thanh Toán VNPAY.")

        tmn_code = self.vnpay_tmn_code.strip()
        secret_key = self.vnpay_hash_secret.strip()

        amount_vnp = int(round(amount)) * 100
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

        # Strip special characters from OrderInfo per VNPAY specification
        clean_ref = (reference or "").replace("/", " ").replace(":", " ")
        clean_info = (order_info or f"Thanh toan giao dich {clean_ref}").replace("/", " ").replace(":", " ")

        default_return = f"{self.get_base_url()}/payment/vnpay/return"

        vnp_params = {
            "vnp_Version": "2.1.0",
            "vnp_Command": "pay",
            "vnp_TmnCode": tmn_code,
            "vnp_Amount": str(amount_vnp),
            "vnp_CurrCode": "VND",
            "vnp_TxnRef": reference,
            "vnp_OrderInfo": clean_info,
            "vnp_OrderType": "other",
            "vnp_Locale": "vn",
            "vnp_ReturnUrl": return_url or default_return,
            "vnp_IpAddr": "127.0.0.1",
            "vnp_CreateDate": now_str,
        }

        # Sort parameters alphabetically by key and format using quote_plus (VNPAY SDK pattern)
        sorted_params = sorted(vnp_params.items())
        hash_data = "&".join([f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in sorted_params])

        # Compute HMAC-SHA512 checksum
        secure_hash = hmac.new(
            secret_key.encode("utf-8"),
            hash_data.encode("utf-8"),
            hashlib.sha512
        ).hexdigest()

        base_url = self._vnpay_get_api_url()
        return f"{base_url}?{hash_data}&vnp_SecureHash={secure_hash}"

    def vnpay_verify_signature(self, response_params, received_hash):
        """
        SINGLE SOURCE OF TRUTH: Validates HMAC-SHA512 signature from VNPAY return / IPN callback.
        """
        self.ensure_one()
        secret_key = (self.vnpay_hash_secret or "").strip()
        if not secret_key or not received_hash:
            return False

        # Filter out hash parameters and null values
        filtered_params = {
            k: v for k, v in response_params.items()
            if k not in ('vnp_SecureHash', 'vnp_SecureHashType') and v is not None
        }

        # Sort alphabetically and format using quote_plus
        sorted_params = sorted(filtered_params.items())
        hash_data = "&".join([f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in sorted_params])

        calculated_hash = hmac.new(
            secret_key.encode("utf-8"),
            hash_data.encode("utf-8"),
            hashlib.sha512
        ).hexdigest()

        return calculated_hash.lower() == received_hash.lower()
