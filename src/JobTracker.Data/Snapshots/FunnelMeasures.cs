using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using JobTracker.Domain;

namespace JobTracker.Data.Snapshots;

/// <summary>What the projection says about one application, as far as the snapshot cares.</summary>
public sealed record AppOutcome(int ApplicationId, ApplicationStatus? Status, bool IsNoReply, int ProblemCount,
    DateOnly DateApplied, DateOnly? InterviewDate, JobType? JobType = null, ResumeVariant? ResumeVariant = null,
    Priority? Priority = null, ReferralType? ReferralType = null);

/// <summary>
/// The funnel numbers, defined exactly as in the snapshot contract. Rates and the average are exact decimals,
/// rounded HALF UP (away from zero), never binary floating point, so Spark can reproduce them digit for digit.
/// </summary>
public sealed record FunnelMeasures(
    int Applications, int Responses, int Interviews, int Offers, int Rejections, int NoReplies,
    int RowsWithInterviewDate, decimal? AvgDaysToInterview,
    decimal? ResponsesRate, decimal? InterviewsRate, decimal? OffersRate, decimal? RejectionsRate, decimal? NoRepliesRate)
{
    public static FunnelMeasures Compute(IEnumerable<AppOutcome> outcomes)
    {
        // An application is anything with a status other than "none" or Not Started (Withdrawn counts).
        var apps = outcomes.Where(o => o.Status is not null and not ApplicationStatus.NotStarted).ToList();
        var n = apps.Count;

        int Count(Func<AppOutcome, bool> match) => apps.Count(match);
        decimal? Rate(int count) => n == 0 ? null : decimal.Round((decimal)count / n, 4, MidpointRounding.AwayFromZero);

        var responses = Count(o => o.Status is ApplicationStatus.Rejected or ApplicationStatus.InterviewScheduled
            or ApplicationStatus.Interviewed or ApplicationStatus.Accepted);
        var interviews = Count(o => o.Status is ApplicationStatus.InterviewScheduled or ApplicationStatus.Interviewed
            or ApplicationStatus.Accepted);
        var offers = Count(o => o.Status == ApplicationStatus.Accepted);
        var rejections = Count(o => o.Status == ApplicationStatus.Rejected);
        var noReplies = Count(o => o.IsNoReply);

        var withInterview = apps.Where(o => o.InterviewDate is not null).ToList();
        decimal? avg = withInterview.Count == 0
            ? null
            : decimal.Round(
                (decimal)withInterview.Sum(o => o.InterviewDate!.Value.DayNumber - o.DateApplied.DayNumber) / withInterview.Count,
                2, MidpointRounding.AwayFromZero);

        return new FunnelMeasures(n, responses, interviews, offers, rejections, noReplies, withInterview.Count, avg,
            Rate(responses), Rate(interviews), Rate(offers), Rate(rejections), Rate(noReplies));
    }

    /// <summary>
    /// One TAB-separated line ending in LF, fields in the contract order. Counts are base-10, rates fixed 4 decimals,
    /// the average fixed 2 decimals, null is \N.
    /// </summary>
    public string HashText()
    {
        static string Int(int v) => v.ToString(CultureInfo.InvariantCulture);
        static string Fixed(decimal? v, string format) => v is null ? GoldHash.Null : v.Value.ToString(format, CultureInfo.InvariantCulture);

        return string.Join('\t',
            Int(Applications), Int(Responses), Int(Interviews), Int(Offers), Int(Rejections), Int(NoReplies),
            Int(RowsWithInterviewDate), Fixed(AvgDaysToInterview, "F2"),
            Fixed(ResponsesRate, "F4"), Fixed(InterviewsRate, "F4"), Fixed(OffersRate, "F4"),
            Fixed(RejectionsRate, "F4"), Fixed(NoRepliesRate, "F4")) + "\n";
    }
}

/// <summary>The canonical gold-content hash text, as defined in the snapshot contract.</summary>
public static class GoldHash
{
    public const string Null = "\\N";

    /// <summary>Rows sorted by application_id; TAB-separated; each row ends in LF.</summary>
    public static string ApplicationStatusText(IEnumerable<AppOutcome> outcomes)
    {
        var sb = new StringBuilder();
        foreach (var o in outcomes.OrderBy(o => o.ApplicationId))
        {
            sb.Append(o.ApplicationId.ToString(CultureInfo.InvariantCulture)).Append('\t')
              .Append(o.Status?.ToString() ?? Null).Append('\t')
              .Append(o.IsNoReply ? '1' : '0').Append('\t')
              .Append(o.ProblemCount.ToString(CultureInfo.InvariantCulture)).Append('\t')
              .Append(o.InterviewDate?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? Null).Append('\n');
        }
        return sb.ToString();
    }

    public static string Sha256Hex(string text) =>
        Convert.ToHexString(SHA256.HashData(new UTF8Encoding(false).GetBytes(text))).ToLowerInvariant();
}
