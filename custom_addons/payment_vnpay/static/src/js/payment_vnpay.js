/** @odoo-module **/

import paymentForm from '@payment/js/payment_form';

paymentForm.include({

    /**
     * Override redirect flow for VNPAY: Direct URL navigation instead of form.submit().
     *
     * Matching VNPAY Python SDK behavior: vnp.get_payment_url() returns full signed URL,
     * then redirect(url). This avoids browser form re-encoding that corrupts HMAC-SHA512 signature.
     *
     * @override method from @payment/js/payment_form
     * @private
     * @param {string} providerCode
     * @param {number} paymentOptionId
     * @param {string} paymentMethodCode
     * @param {object} processingValues
     * @return {void}
     */
    _processRedirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'vnpay' || !processingValues.vnpay_checkout_url) {
            return this._super(...arguments);
        }
        // VNPAY: Direct URL redirect - exact same behavior as SDK's redirect(vnpay_payment_url)
        window.location.href = processingValues.vnpay_checkout_url;
    },

});
