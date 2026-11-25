using System.Globalization;
using JobTracker.Domain;

namespace JobTracker.Data.Importing;

/// <summary>Thrown when the file is not a tracker CSV at all (empty, or a required column is missing).</summary>
public sealed class ImportFormatException(string message) : Exception(message);

/// <summary>One tracker row, turned into typed values and ready to import.</summary>
/// <param name="SourceRow">Row number in the CSV file; the header is row 1, so the first data row is 2.</param>
/// <param name="SourceId">The reader's application_id, kept only so a report can point at the row.</param>
public sealed record ImportRow(
    int SourceRow, string SourceId, string CompanyName, string JobTitle, DateOnly DateApplied,
    JobType? JobType, string? Location, ResumeVariant? ResumeVariant, ReferralType? ReferralType,
    ApplicationStatus Status, DateOnly? InterviewDate, Priority? Priority);

/// <summary>A row that was not imported, and why. Nothing is ever guessed to rescue a row.</summary>
public sealed record SkippedRow(int SourceRow, string SourceId, string Reason);

public sealed record TrackerParseResult(IReadOnlyList<ImportRow> Rows, IReadOnlyList<SkippedRow> Skipped);

/// <summary>
/// Reads the text of the tracker reader's <c>clean_rows.csv</c>. Pure: no files, no database.
/// A bad row is skipped and reported, never repaired. A wrong file (missing column) throws.
/// </summary>
public static class TrackerCsvParser
{
    private static readonly string[] RequiredColumns =
    [
        "application_id", "company_name", "job_title", "date_applied", "job_type", "location",
        "resume_variant", "referral_type", "application_status", "interview_date", "priority",
    ];

    // The words the reader writes. A test checks that every label the app shows is in these tables.
    internal static readonly Dictionary<string, JobType> JobTypes = new(StringComparer.Ordinal)
    {
        ["Data Analyst"] = JobType.DataAnalyst,
        ["BI Analyst"] = JobType.BiAnalyst,
        ["Business Analyst"] = JobType.BusinessAnalyst,
        ["Data Scientist"] = JobType.DataScientist,
        ["ML/AI Engineer"] = JobType.MlAiEngineer,
        ["Data Engineer"] = JobType.DataEngineer,
        ["Software Engineer"] = JobType.SoftwareEngineer,
        ["New Grad Program"] = JobType.NewGradProgram,
        ["Other"] = JobType.Other,
    };

    internal static readonly Dictionary<string, ResumeVariant> ResumeVariants = new(StringComparer.Ordinal)
    {
        ["Data Analytics"] = ResumeVariant.DataAnalytics,
        ["Backend SWE"] = ResumeVariant.BackendSwe,
        ["Business Intelligence"] = ResumeVariant.BusinessIntelligence,
        ["ML/Data Science"] = ResumeVariant.MlDataScience,
        ["Data Engineering"] = ResumeVariant.DataEngineering,
        ["Automation"] = ResumeVariant.Automation,
        ["Frontend SWE"] = ResumeVariant.FrontendSwe,
    };

    internal static readonly Dictionary<string, Priority> Priorities = new(StringComparer.Ordinal)
    {
        ["Top Priority"] = Priority.TopPriority,
        ["Medium Priority"] = Priority.MediumPriority,
        ["Low Priority"] = Priority.LowPriority,
    };

    internal static readonly Dictionary<string, ReferralType> ReferralTypes = new(StringComparer.Ordinal)
    {
        ["Networking Contact"] = ReferralType.NetworkingContact,
        ["Cold Apply"] = ReferralType.ColdApply,
    };

    // "No Reply" is something the app works out itself (Applied for over 30 days), so it is stored as Applied.
    private static readonly Dictionary<string, ApplicationStatus> Statuses = new(StringComparer.Ordinal)
    {
        ["Not Started"] = ApplicationStatus.NotStarted,
        ["Applied"] = ApplicationStatus.Applied,
        ["No Reply"] = ApplicationStatus.Applied,
        ["Withdrawn"] = ApplicationStatus.Withdrawn,
        ["Rejected"] = ApplicationStatus.Rejected,
        ["Interview Scheduled"] = ApplicationStatus.InterviewScheduled,
        ["Interviewed"] = ApplicationStatus.Interviewed,
        ["Accepted"] = ApplicationStatus.Accepted,
    };

    public static TrackerParseResult Parse(string csvText)
    {
        var table = Csv.Parse(csvText.TrimStart('﻿'));
        if (table.Count == 0)
            throw new ImportFormatException("The file is empty: no header row.");

        var header = table[0];
        var column = new Dictionary<string, int>(StringComparer.Ordinal);
        for (var i = 0; i < header.Length; i++)
            column[header[i].Trim()] = i;

        var missing = RequiredColumns.Where(c => !column.ContainsKey(c)).ToList();
        if (missing.Count > 0)
            throw new ImportFormatException($"Missing required column(s): {string.Join(", ", missing)}.");

        var rows = new List<ImportRow>();
        var skipped = new List<SkippedRow>();

        for (var r = 1; r < table.Count; r++)
        {
            var cells = table[r];
            string Cell(string name) => column[name] < cells.Length ? cells[column[name]].Trim() : "";

            var sourceRow = r + 1;
            var sourceId = Cell("application_id");
            var error = TryReadRow(Cell, sourceRow, sourceId, out var row);
            if (error is null) rows.Add(row!);
            else skipped.Add(new SkippedRow(sourceRow, sourceId, error));
        }

        return new TrackerParseResult(rows, skipped);
    }

    /// <summary>Returns null and the row on success, or the reason the row cannot be imported.</summary>
    private static string? TryReadRow(Func<string, string> cell, int sourceRow, string sourceId, out ImportRow? row)
    {
        row = null;

        var company = cell("company_name");
        if (company.Length == 0) return "Company name is blank.";
        var title = cell("job_title");
        if (title.Length == 0) return "Job title is blank.";

        var dateText = cell("date_applied");
        if (dateText.Length == 0) return "Date applied is blank.";
        if (!TryDate(dateText, out var dateApplied)) return $"Date applied is not a valid date (expected YYYY-MM-DD): '{dateText}'.";

        DateOnly? interviewDate = null;
        var interviewText = cell("interview_date");
        if (interviewText.Length > 0)
        {
            if (!TryDate(interviewText, out var parsed)) return $"Interview date is not a valid date (expected YYYY-MM-DD): '{interviewText}'.";
            interviewDate = parsed;
        }

        if (!Statuses.TryGetValue(cell("application_status"), out var status))
            return $"Unknown status: '{cell("application_status")}'.";

        var jobTypeText = cell("job_type");
        JobType? jobType = null;
        if (jobTypeText.Length > 0)
        {
            if (!JobTypes.TryGetValue(jobTypeText, out var t)) return $"Unknown job type: '{jobTypeText}'.";
            jobType = t;
        }

        ResumeVariant? variant = null;
        var variantText = cell("resume_variant");
        if (variantText.Length > 0 && variantText != "Unspecified")
        {
            if (!ResumeVariants.TryGetValue(variantText, out var v)) return $"Unknown resume variant: '{variantText}'.";
            variant = v;
        }

        Priority? priority = null;
        var priorityText = cell("priority");
        if (priorityText.Length > 0 && priorityText != "Unspecified")
        {
            if (!Priorities.TryGetValue(priorityText, out var p)) return $"Unknown priority: '{priorityText}'.";
            priority = p;
        }

        ReferralType? referral = null;
        var referralText = cell("referral_type");
        if (referralText.Length > 0)
        {
            if (!ReferralTypes.TryGetValue(referralText, out var f)) return $"Unknown referral type: '{referralText}'.";
            referral = f;
        }

        var location = cell("location");
        row = new ImportRow(sourceRow, sourceId, company, title, dateApplied, jobType,
            location.Length == 0 ? null : location, variant, referral, status, interviewDate, priority);
        return null;
    }

    private static bool TryDate(string text, out DateOnly date) =>
        DateOnly.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);
}
