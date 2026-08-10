# -*- coding: utf-8 -*-
# from odoo import http


# class Recruitment(http.Controller):
#     @http.route('/recruitment/recruitment', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/recruitment/recruitment/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('recruitment.listing', {
#             'root': '/recruitment/recruitment',
#             'objects': http.request.env['recruitment.recruitment'].search([]),
#         })

#     @http.route('/recruitment/recruitment/objects/<model("recruitment.recruitment"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('recruitment.object', {
#             'object': obj
#         })

