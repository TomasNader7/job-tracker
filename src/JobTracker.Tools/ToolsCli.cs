using System.Globalization;
using JobTracker.Data;
using JobTracker.Data.Seeding;
using JobTracker.Data.Snapshots;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Tools;

/// <summary>
/// Command line for the synthetic demo lane: seed-demo and export-snapshot.
/// Exit codes: 0 ok, 1 usage error, 2 refused (wrong database), 3 failure.
/// </summary>
public static class ToolsCli
{
    public const string DefaultConnection =
        @"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Demo;Trusted_Connection=True;TrustServerCertificate=True;";

    /// <summary>The fixed reference date of the Phase 0 synthetic data. Pinned so every run is reproducible.</summary>
    public const string DefaultAsOf = "2026-11-16";

    private const string Usage = """
        Usage:
          JobTracker.Tools seed-demo [--connection <cs>] [--csv-dir <dir>] [--as-of yyyy-MM-dd] [--reset]
          JobTracker.Tools export-snapshot [--connection <cs>] [--out <dir>] [--as-of <date|timestamp>]
                                           [--exported-at <timestamp>] [--snapshot-id <id>]
        Only the LocalDB database named JobTracker_Demo is allowed. Anything else is refused.
        """;

    private static readonly Dictionary<string, (string[] Values, string[] Flags)> Commands = new()
    {
        ["seed-demo"] = (["--connection", "--csv-dir", "--as-of"], ["--reset"]),
        ["export-snapshot"] = (["--connection", "--out", "--as-of", "--exported-at", "--snapshot-id"], []),
    };

    public static int Run(string[] args, TextWriter output, TextWriter error) =>
        Run(args, output, error, DemoDatabaseGuard.DemoDatabase);

    /// <param name="allowedDatabase">Only tests pass anything but JobTracker_Demo.</param>
    public static int Run(string[] args, TextWriter output, TextWriter error, string allowedDatabase)
    {
        if (args.Length == 0 || !Commands.TryGetValue(args[0], out var spec))
            return Usage1(error, args.Length == 0 ? "No command given." : $"Unknown command '{args[0]}'.");

        var options = new Dictionary<string, string>();
        for (var i = 1; i < args.Length; i++)
        {
            var name = args[i];
            if (spec.Flags.Contains(name)) { options[name] = "true"; continue; }
            if (!spec.Values.Contains(name)) return Usage1(error, $"Unknown option '{name}' for {args[0]}.");
            if (i + 1 >= args.Length) return Usage1(error, $"Option '{name}' needs a value.");
            options[name] = args[++i];
        }

        var connection = options.GetValueOrDefault("--connection", DefaultConnection);
        try
        {
            // First thing, before any database is opened.
            DemoDatabaseGuard.Require(connection, allowedDatabase);
        }
        catch (DemoGuardException ex)
        {
            error.WriteLine(ex.Message);
            return 2;
        }

        try
        {
            return args[0] == "seed-demo"
                ? SeedDemo(options, connection, allowedDatabase, output, error)
                : ExportSnapshot(options, connection, allowedDatabase, output, error);
        }
        catch (DemoGuardException ex)
        {
            error.WriteLine(ex.Message);
            return 2;
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or ArgumentException or DbUpdateException)
        {
            error.WriteLine($"Failed: {ex.Message}");
            return 3;
        }
    }

    private static int SeedDemo(Dictionary<string, string> options, string connection, string allowedDatabase,
        TextWriter output, TextWriter error)
    {
        if (!DateOnly.TryParseExact(options.GetValueOrDefault("--as-of", DefaultAsOf), "yyyy-MM-dd",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out var asOf))
            return Usage1(error, "Bad --as-of. Use yyyy-MM-dd.");

        var csvDir = options.GetValueOrDefault("--csv-dir") ?? Path.Combine(RepoRoot(), "data", "synthetic");
        using var db = NewContext(connection);

        if (options.ContainsKey("--reset"))
        {
            output.WriteLine($"Resetting {allowedDatabase} (dropping and recreating it).");
            db.Database.EnsureDeleted();
        }
        db.Database.Migrate();

        var result = new DemoSeeder(allowedDatabase).Seed(db, DemoSeeder.ReadRows(csvDir), asOf);
        output.WriteLine($"Seeded {result.Applications} applications, {result.Events} events " +
                         $"({result.Corrections} corrections), {result.ProblemCount} projection problems. As of {asOf:yyyy-MM-dd}.");
        return 0;
    }

    private static int ExportSnapshot(Dictionary<string, string> options, string connection, string allowedDatabase,
        TextWriter output, TextWriter error)
    {
        if (!TryParseInstant(options.GetValueOrDefault("--as-of", DefaultAsOf), out var asOf))
            return Usage1(error, "Bad --as-of. Use yyyy-MM-dd or an ISO timestamp such as 2026-11-16T00:00:00Z.");

        DateTimeOffset exportedAt;
        if (options.TryGetValue("--exported-at", out var exportedText))
        {
            if (!TryParseInstant(exportedText, out exportedAt))
                return Usage1(error, "Bad --exported-at. Use an ISO timestamp such as 2026-11-17T09:30:00Z.");
        }
        else
        {
            var now = DateTimeOffset.UtcNow;
            exportedAt = new DateTimeOffset(now.UtcTicks - now.UtcTicks % 10, TimeSpan.Zero); // whole microseconds
        }

        var outDir = options.GetValueOrDefault("--out") ?? Path.Combine(RepoRoot(), "snapshots");
        using var db = NewContext(connection);
        var r = new SnapshotExporter(allowedDatabase).Export(db, outDir,
            new ExportOptions(asOf, exportedAt, options.GetValueOrDefault("--snapshot-id")));

        output.WriteLine($"Snapshot {r.SnapshotId}{(r.AlreadyExisted ? " (already existed, identical data, left as is)" : "")}");
        output.WriteLine($"  folder: {r.Folder}");
        output.WriteLine($"  {r.Applications} applications, {r.Events} events, as of {SnapshotFormat.Timestamp(asOf)}");
        var m = r.Measures;
        output.WriteLine($"  Applications {m.Applications}, Responses {m.Responses}, Interviews {m.Interviews}, Offers {m.Offers}, " +
                         $"Rejections {m.Rejections}, No Replies {m.NoReplies}");
        return 0;
    }

    private static bool TryParseInstant(string text, out DateTimeOffset value) =>
        DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture,
            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out value);

    private static JobTrackerDbContext NewContext(string connection) =>
        new(new DbContextOptionsBuilder<JobTrackerDbContext>().UseSqlServer(connection).Options);

    private static int Usage1(TextWriter error, string message)
    {
        error.WriteLine(message);
        error.WriteLine(Usage);
        return 1;
    }

    /// <summary>The folder that contains JobTracker.sln, found by walking up from the current directory.</summary>
    private static string RepoRoot()
    {
        foreach (var start in new[] { Directory.GetCurrentDirectory(), AppContext.BaseDirectory })
        {
            var dir = new DirectoryInfo(start);
            while (dir is not null)
            {
                if (File.Exists(Path.Combine(dir.FullName, "JobTracker.sln"))) return dir.FullName;
                dir = dir.Parent;
            }
        }
        throw new InvalidOperationException("Could not find JobTracker.sln. Run from inside the repo or pass --csv-dir / --out.");
    }
}
