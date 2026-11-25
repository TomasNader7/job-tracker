using System.Globalization;
using System.Text.RegularExpressions;

namespace JobTracker.Data.Snapshots;

/// <summary>Formats and identity rules of snapshot contract v2 (docs/snapshot-contract.md).</summary>
public static partial class SnapshotFormat
{
    public const string ContractVersion = "2";
    public const string Lane = "synthetic";

    public static readonly string[] ApplicationColumns =
    [
        "application_id", "company_name", "job_title", "date_applied", "job_type", "resume_variant", "priority",
        "referral_type", "location", "expected_projected_status", "expected_is_no_reply",
    ];

    public static readonly string[] ApplicationColumnTypes =
        ["int64", "string", "string", "date", "string", "string", "string", "string", "string", "string", "int"];

    public static readonly string[] EventColumns =
    [
        "event_sequence", "application_id", "previous_status", "new_status", "effective_at", "recorded_at",
        "source", "supersedes_event_sequence", "note",
    ];

    public static readonly string[] EventColumnTypes =
        ["int64", "int64", "string", "string", "timestamp_utc_us", "timestamp_utc_us", "string", "int64", "string"];

    /// <summary>UTC, six fractional digits, for example 2026-11-16T00:00:00.000000Z.</summary>
    public static string Timestamp(DateTimeOffset value) =>
        value.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture);

    /// <summary>True if the value can be written exactly at microsecond precision (10 ticks = 1 microsecond).</summary>
    public static bool IsRepresentable(DateTimeOffset value) => value.UtcTicks % 10 == 0;

    public static bool IsSafeSnapshotId(string? id) => id is not null && SafeId().IsMatch(id);

    [GeneratedRegex(@"^[a-z0-9][a-z0-9-]{2,63}\z")]
    private static partial Regex SafeId();
}
