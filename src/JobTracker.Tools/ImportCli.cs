using JobTracker.Data;
using JobTracker.Data.Importing;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Tools;

/// <summary>
/// import-tracker: reads the tracker reader's clean_rows.csv into the app database.
/// Dry run by default. A real write (--apply) takes a backup first and, on the real JobTracker database,
/// also needs --allow-real-db. The report shows row numbers and ids only, never company names.
/// Exit codes: 0 ok, 1 usage error, 2 refused (guard), 3 failure.
/// </summary>
public static class ImportCli
{
    public const string DefaultConnection =
        @"Server=(localdb)\MSSQLLocalDB;Database=JobTracker;Trusted_Connection=True;TrustServerCertificate=True;";

    private const string Usage = """
        Usage:
          JobTracker.Tools import-tracker --csv <clean_rows.csv> [--connection <cs>] [--apply] [--allow-real-db]
                                          [--backup-dir <dir>]
        Dry run unless --apply is given. --apply backs the database up first. Writing to the real
        JobTracker database also needs --allow-real-db. Default backup folder: <your profile>\job-tracker-backups.
        """;

    private static readonly string[] ValueOptions = ["--csv", "--connection", "--backup-dir"];
    private static readonly string[] FlagOptions = ["--apply", "--allow-real-db"];

    /// <param name="args">The options only, without the command word.</param>
    public static int Run(string[] args, TextWriter output, TextWriter error) =>
        Run(args, output, error, TimeProvider.System);

    public static int Run(string[] args, TextWriter output, TextWriter error, TimeProvider clock)
    {
        var options = new Dictionary<string, string>();
        for (var i = 0; i < args.Length; i++)
        {
            var name = args[i];
            if (FlagOptions.Contains(name)) { options[name] = "true"; continue; }
            if (!ValueOptions.Contains(name)) return Usage1(error, $"Unknown option '{name}'.");
            if (i + 1 >= args.Length) return Usage1(error, $"Option '{name}' needs a value.");
            options[name] = args[++i];
        }

        var apply = options.ContainsKey("--apply");
        var connection = options.GetValueOrDefault("--connection", DefaultConnection);

        try
        {
            // First thing, before any file or database is opened.
            ImportDatabaseGuard.Require(connection, apply, options.ContainsKey("--allow-real-db"));
        }
        catch (ImportGuardException ex)
        {
            error.WriteLine(ex.Message);
            return 2;
        }

        if (!options.TryGetValue("--csv", out var csvPath))
            return Usage1(error, "Missing --csv <clean_rows.csv>.");
        if (!File.Exists(csvPath))
            return Usage1(error, "CSV file not found.");

        try
        {
            var parsed = TrackerCsvParser.Parse(File.ReadAllText(csvPath));

            if (apply)
            {
                var folder = options.GetValueOrDefault("--backup-dir") ?? DefaultBackupDir();
                string backup;
                try
                {
                    backup = DatabaseBackup.Create(connection, folder, clock.GetUtcNow());
                }
                catch (Exception ex)
                {
                    error.WriteLine($"Backup failed, nothing was written: {ex.Message}");
                    return 3;
                }
                output.WriteLine($"Backup saved: {backup}");
            }

            using var db = new JobTrackerDbContext(new DbContextOptionsBuilder<JobTrackerDbContext>().UseSqlServer(connection).Options);
            var report = new TrackerImporter(db, clock).Import(parsed, write: apply);
            Print(report, output);
            return 0;
        }
        catch (Exception ex) when (ex is ImportFormatException or InvalidOperationException or IOException
                                       or ArgumentException or DbUpdateException or Microsoft.Data.SqlClient.SqlException)
        {
            error.WriteLine($"Failed, nothing was written: {ex.Message}");
            return 3;
        }
    }

    private static void Print(ImportReport report, TextWriter output)
    {
        output.WriteLine(report.Written ? "Import applied." : "Dry run: nothing was saved.");
        var flagged = report.Imported.Sum(r => r.FlaggedEvents);
        var total = report.Imported.Sum(r => r.EventCount);
        output.WriteLine(report.Written
            ? $"  {report.Imported.Count} imported ({total} events, {flagged} with a placeholder date)"
            : $"  {report.Imported.Count} would be imported ({total} events, {flagged} with a placeholder date)");
        output.WriteLine($"  {report.Skipped.Count} skipped");
        foreach (var s in report.Skipped)
            output.WriteLine($"    Row {s.SourceRow} (id {s.SourceId}): {s.Reason}");
        output.WriteLine($"  {report.Warnings.Count} warnings");
        foreach (var w in report.Warnings)
            output.WriteLine($"    Row {w.SourceRow} (id {w.SourceId}): {w.Message}");
    }

    private static string DefaultBackupDir() =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "job-tracker-backups");

    private static int Usage1(TextWriter error, string message)
    {
        error.WriteLine(message);
        error.WriteLine(Usage);
        return 1;
    }
}
