from odoo import models,fields

class RecruitmentCandidate(models.Model):
    _name = "recruitment.candidate"
    _description = "Recruitment Candidate"

    name = fields.Char(
        string="Name",
        required=True,
    )
    
    email = fields.Char(
        string="Email",
        required=True,
    )

    phone = fields.Char(
        string="Phone",
        required=True,
    )

    address = fields.Text(
        string = "Address"
    )

    date_of_birth = fields.Date(
        string = "Date of Birth"
    )

    gender = fields.Selection(
        selection=[
            ('male', 'Male'),
            ('female', 'Female'),
        ],
        string="Gender",
    )

    application_date = fields.Date(
        string = "Application Date",
        default = fields.Date.today(),
        required=True,
    )

    expected_salary = fields.Float(
        string = "Expected Salary",
    )

    experience_years = fields.Float(
        string = "Experience Years",
        required=True,
    )

    education = fields.Char(
        string = "Education",
    )

    skills = fields.Text(
        string = "Skills",
    
    )

    cv_file = fields.Binary(
        string = "CV File",
        attachment=True,
    )

    cv_filename = fields.Char(
        string = "CV Filename",
    )

    note = fields.Text(
        string = "Note",
    )

    job_position_id = fields.Many2One(
        string = "Job Position",
        comodel_name="recruitment.job.position",
    )   

    active = fields.Boolean(
        string = "Active",
        default = True,
    )   

    

    
        