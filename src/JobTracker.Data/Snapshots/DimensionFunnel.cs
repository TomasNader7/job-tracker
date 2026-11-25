using System.Globalization;
using System.Text;

namespace JobTracker.Data.Snapshots;

/// <summary>One row of gold_funnel_by_dimension: the funnel for applications that share one value of one field.</summary>
public sealed record DimensionRow(string Dimension, string Value, FunnelMeasures Measures);

/// <summary>
/// The funnel split by job type, resume variant, priority and referral type (snapshot contract v2).
/// Each group uses exactly the overall measure rules. A missing value is the word "Unspecified".
/// </summary>
public static class DimensionFunnel
{
    public const string Unspecified = "Unspecified";

    public static readonly string[] Dimensions = ["job_type", "resume_variant", "priority", "referral_type"];

    public static IReadOnlyList<DimensionRow> Compute(IEnumerable<AppOutcome> outcomes)
    {
        var all = outcomes.ToList();
        var selectors = new (string Name, Func<AppOutcome, string> Value)[]
        {
            (Dimensions[0], o => o.JobType?.ToString() ?? Unspecified),
            (Dimensions[1], o => o.ResumeVariant?.ToString() ?? Unspecified),
            (Dimensions[2], o => o.Priority?.ToString() ?? Unspecified),
            (Dimensions[3], o => o.ReferralType?.ToString() ?? Unspecified),
        };

        var rows = new List<DimensionRow>();
        foreach (var (name, value) in selectors)
        {
            foreach (var group in all.GroupBy(value).OrderBy(g => g.Key, StringComparer.Ordinal))
            {
                var measures = FunnelMeasures.Compute(group);
                if (measures.Applications > 0) rows.Add(new DimensionRow(name, group.Key, measures));
            }
        }

        return rows;
    }

    /// <summary>
    /// Canonical text for the gold hash: per row, TAB-separated dimension, value, six counts, then the five rates
    /// (fixed 4 decimals), ending in LF. Rows are in the order Compute returns (dimension order, then ordinal value).
    /// </summary>
    public static string HashText(IReadOnlyList<DimensionRow> rows)
    {
        static string Int(int v) => v.ToString(CultureInfo.InvariantCulture);
        static string Rate(decimal? v) => v is null ? GoldHash.Null : v.Value.ToString("F4", CultureInfo.InvariantCulture);

        var sb = new StringBuilder();
        foreach (var r in rows)
        {
            var m = r.Measures;
            sb.Append(string.Join('\t', r.Dimension, r.Value, Int(m.Applications), Int(m.Responses), Int(m.Interviews),
                Int(m.Offers), Int(m.Rejections), Int(m.NoReplies), Rate(m.ResponsesRate), Rate(m.InterviewsRate),
                Rate(m.OffersRate), Rate(m.RejectionsRate), Rate(m.NoRepliesRate))).Append('\n');
        }

        return sb.ToString();
    }
}
