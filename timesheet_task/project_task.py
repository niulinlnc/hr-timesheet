# -*- coding: utf-8 -*-
##############################################################################
#
#    Author: Nicolas Bessi
#    Copyright 2013 Camptocamp SA
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from openerp import _, api, fields, models
from openerp.exceptions import Warning as UserError
from openerp.osv import orm, fields as fieldsv

TASK_WATCHERS = [
    'work_ids',
    'remaining_hours',
    'effective_hours',
    'planned_hours'
]
TIMESHEET_WATCHERS = [
    'unit_amount',
    'product_uom_id',
    'account_id',
    'task_id'
]


class ProjectTask(models.Model):
    _inherit = "project.task"
    _name = "project.task"

    work_ids = fields.One2many(
        comodel_name='hr.analytic.timesheet',
        inverse_name='task_id',
        string='Work done',
    )
    effective_hours = fields.Float(
        compute='_compute_progress_rate',
        string='Time Spent',
        help="Computed using the sum of the task work done (timesheet lines "
             "associated on this task).",
        store=True,
    )
    delay_hours = fields.Float(
        compute='_compute_progress_rate',
        string='Deduced Hours',
        help="Computed as difference between planned hours by the project "
             "manager and the total hours of the task.",
        store=True,
    )
    total_hours = fields.Float(
        compute='_compute_progress_rate',
        string='Total Time',
        help="Computed as: Time Spent + Remaining Time.",
        store=True,
    )
    progress = fields.Float(
        compute='_compute_progress_rate',
        string='Progress',
        group_operator="avg",
        help="If the task has a progress of 99.99% you should close the "
             "task if it's finished or reevaluate the time",
        store=True,
    )
    remaining_hours = fields.Float(
        compute='_compute_progress_rate',
        string='Remaining Time',
        help="Computed as: Planned Hours - Time Spent",
        store=True,
    )

    @api.multi
    @api.depends('work_ids', 'remaining_hours', 'effective_hours',
                 'planned_hours',
                 'work_ids.unit_amount', 'work_ids.product_uom_id',
                 'work_ids.account_id', 'work_ids.task_id')
    def _compute_progress_rate(self):
        for task in self:
            task.effective_hours = sum(task.work_ids.mapped('unit_amount'))
            task.remaining_hours = task.planned_hours - task.effective_hours
            task.total_hours = (
                task.remaining_hours > 0.0 and
                task.remaining_hours + task.effective_hours or
                task.effective_hours)
            task.delay_hours = task.total_hours - task.planned_hours
            task.progress = task.total_hours and round(
                100.0 * task.effective_hours / task.total_hours, 2) or 0.0


class HrAnalyticTimesheet(orm.Model):

    """
    Add field:
    - hr_analytic_timesheet_id:
    This field is added to make sure a hr.analytic.timesheet can be used
    instead of a project.task.work.

    This field will always return false as we want to by pass next operations
    in project.task write method.

    Without this field, it is impossible to write a project.task in which
    work_ids is empty as a check on it would raise an AttributeError.

    This is because, in project_timesheet module, project.task's write method
    checks if there is an hr_analytic_timesheet_id on each work_ids.

        (project_timesheet.py, line 250, in write)
        if not task_work.hr_analytic_timesheet_id:
            continue

    But as we redefine work_ids to be a relation to hr_analytic_timesheet
    instead of project.task.work, hr_analytic_timesheet doesn't exists
    in hr_analytic_timesheet... so it fails.

    An other option would be to monkey patch the project.task's write method...
    As this method doesn't fit with the change of work_ids relation in model.
    """
    _inherit = "hr.analytic.timesheet"
    _name = "hr.analytic.timesheet"

    def on_change_unit_amount(
            self, cr, uid, sheet_id, prod_id, unit_amount, company_id,
            unit=False, journal_id=False, task_id=False, to_invoice=False,
            project_id=False, context=None):
        res = super(HrAnalyticTimesheet, self).on_change_unit_amount(
            cr, uid, sheet_id, prod_id, unit_amount, company_id, unit,
            journal_id, context=context)
        p = False
        if 'value' in res:
            if task_id:
                task_obj = self.pool['project.task']
                p = task_obj.browse(cr, uid, task_id,
                                    context=context).project_id
            elif project_id:
                p = self.pool['project.project'].browse(
                    cr, uid, project_id, context=context)
            if p:
                res['value']['account_id'] = p.analytic_account_id.id
                if p.to_invoice and not to_invoice:
                    res['value']['to_invoice'] = p.to_invoice.id
        return res

    def _get_dummy_hr_analytic_timesheet_id(
            self, cr, uid, ids, names, arg, context=None):
        """Ensure all hr_analytic_timesheet_id is always False"""
        return dict.fromkeys(ids, False)

    @api.multi
    def on_change_account_id(self, account_id, user_id=False):
        ''' Validate the relation between the project and the task.
            Task must be belong to the project.
        '''
        res = super(HrAnalyticTimesheet, self)\
            .on_change_account_id(account_id=account_id, user_id=user_id)

        if 'value' not in res:
            res['value'] = {}

        task_id = False
        if account_id:
            project_obj = self.env["project.project"]
            projects = project_obj.search([('analytic_account_id',
                                            '=', account_id)])
            if projects:
                assert len(projects) == 1
                project = projects[0]
                if len(project.tasks) == 1:
                    task_id = project.tasks[0].id

        res['value']['task_id'] = task_id
        return res

    _columns = {
        'hr_analytic_timesheet_id': fieldsv.function(
            _get_dummy_hr_analytic_timesheet_id, string='Related Timeline Id',
            type='boolean')
    }


class AccountAnalyticLine(models.Model):
    """We add task_id on AA and manage update of linked task indicators"""
    _inherit = "account.analytic.line"

    task_id = fields.Many2one(comodel_name='project.task', string='Task')
    hours = fields.Float(related='unit_amount')

    @api.multi
    @api.constrains('task_id', 'account_id')
    def _check_task_project(self):
        for line in self:
            if line.task_id and line.account_id:
                if line.task_id.project_id.analytic_account_id.id != \
                        line.account_id.id:
                    raise UserError(
                        _('Error! Task must belong to the project.'))
        return True
