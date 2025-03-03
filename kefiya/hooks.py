# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt
from __future__ import unicode_literals

from . import __version__ as app_version  # noqa: F401

app_name = "kefiya"
app_title = "Kefiya"
app_publisher = "Phamos GmbH"
app_description = "FinTS Connector for ERPNext (Germany)"
app_icon = "fa fa-university"
app_color = "#3498db"
app_email = "support@phamos.eu"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/kefiya/css/kefiya.css"
# app_include_js = "/assets/kefiya/js/kefiya.js"

# include js, css files in header of web template
# web_include_css = "/assets/kefiya/css/kefiya.css"
# web_include_js = "/assets/kefiya/js/kefiya.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "kefiya/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Payment Request": "public/js/payment_request.js"
}
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#    "Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------
after_migrate = "kefiya.setup.install.after_migrate"
after_install = [
    "kefiya.setup.install.after_migrate",
    "kefiya.utils.install.after_install"
]
before_uninstall = "kefiya.setup.install.before_uninstall"
before_install = "kefiya.utils.install.before_install"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "kefiya.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
#     "Event": "frappe.desk.doctype.event.event.get_permission_query_conditions", # noqa: E501
# }
#
# has_permission = {
#     "Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
    "Journal Entry": "kefiya.overrides.journal_entry.journal_entry.CustomJournalEntry",
    "Bank Transaction": "kefiya.overrides.bank_transaction.bank_transaction.CustomBankTransaction",
}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
#     "*": {
#         "on_update": "method",
#         "on_cancel": "method",
#         "on_trash": "method"
#         }
# }
doc_events = {
    "Bank Account": {
        "validate": "kefiya.utils.bank_account_controller.validate_unique_iban"  # noqa: E501
    }
}


# Scheduled Tasks
# ---------------

# scheduler_events = {
#     "all": [
#         "kefiya.tasks.all"
#     ],
#     "daily": [
#         "kefiya.tasks.daily"
#     ],
#     "hourly": [
#         "kefiya.tasks.hourly"
#     ],
#     "weekly": [
#         "kefiya.tasks.weekly"
#     ]
#     "monthly": [
#         "kefiya.tasks.monthly"
#     ]
# }

scheduler_events = {
    "cron": {
        "*/20 * * * *": [
            "kefiya.kefiya.doctype.kefiya_schedule.kefiya_schedule.scheduled_import_fints_payments"  # noqa: E501
        ]
    }
}

# Testing
# -------

# before_tests = "kefiya.install.before_tests"

# Overriding Methods
# ------------------------------
#
override_whitelisted_methods = {
    "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_journal_entry_bts": "kefiya.overrides.bank_reconciliation_tool.bank_reconciliation_tool.custom_create_journal_entry_bts"
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
#     "Task": "kefiya.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]


# User Data Protection
# --------------------

# user_data_fields = [
#     {
#         "doctype": "{doctype_1}",
#         "filter_by": "{filter_by}",
#         "redact_fields": ["{field_1}", "{field_2}"],
#         "partial": 1,
#     },
#     {
#         "doctype": "{doctype_2}",
#         "filter_by": "{filter_by}",
#         "partial": 1,
#     },
#     {
#         "doctype": "{doctype_3}",
#         "strict": False,
#     },
#     {
#         "doctype": "{doctype_4}"
#     }
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
#     "kefiya.auth.validate"
# ]
