"""Export utilities for grades and submissions."""

import csv

import openpyxl
from django.http import HttpResponse


def export_submissions_csv(submissions):
    """
    Generate CSV response from queryset of Submission objects.

    Args:
        submissions: QuerySet of Submission objects with related data

    Returns:
        HttpResponse with CSV attachment
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="grades.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "Student",
            "Course",
            "Assignment",
            "Problem",
            "Status",
            "Submitted At",
        ]
    )

    for submission in submissions.select_related("user", "problem__assignment__course"):
        writer.writerow(
            [
                submission.user.get_full_name() or submission.user.username,
                submission.problem.assignment.course.title,
                submission.problem.assignment.title,
                submission.problem.title,
                submission.get_status_display(),
                submission.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )

    return response


def export_submissions_excel(submissions):
    """
    Generate Excel response from queryset of Submission objects.

    Args:
        submissions: QuerySet of Submission objects with related data

    Returns:
        HttpResponse with Excel attachment
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Grades"

    headers = ["Student", "Course", "Assignment", "Problem", "Status", "Submitted At"]
    ws.append(headers)

    for submission in submissions.select_related("user", "problem__assignment__course"):
        ws.append(
            [
                submission.user.get_full_name() or submission.user.username,
                submission.problem.assignment.course.title,
                submission.problem.assignment.title,
                submission.problem.title,
                submission.get_status_display(),
                submission.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="grades.xlsx"'
    wb.save(response)
    return response
