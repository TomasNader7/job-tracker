using JobTracker.Data;
using JobTracker.Tools;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Own throwaway database (JobTracker_Test_ImportCli). Never touches JobTracker.</summary>
public sealed class ImportCliTests : IDisposable
{
    private const string Db = "JobTracker_Test_ImportCli";
    private static readonly string TestConnection = $@"Server=(localdb)\MSSQLLocalDB;Database={Db};Trusted_Connection=True;TrustServerCertificate=True;";
    private const string RealConnection = @"Server=(localdb)\MSSQLLocalDB;Database=JobTracker;Trusted_Connection=True;TrustServerCertificate=True;";

    private const string Header =
        "application_id,company_name,job_title,date_applied,job_type,location,resume_variant,referral_type,application_status,interview_date,priority";
    private const string Applied = "1,Acme,Analyst,2026-10-05,Data Analyst,\"Austin, TX\",Data Analytics,Networking Contact,Applied,,Top Priority";
    private const string Rejected = "2,Beta,Engineer,2026-10-06,Software Engineer,,Backend SWE,Cold Apply,Rejected,,Unspecified";
    private const string NoDate = "3,Gamma,Scientist,,Data Scientist,,ML/Data Science,Cold Apply,Applied,,Low Priority";

    private readonly string _dir = Path.Combine(Path.GetTempPath(), "jt-importcli-" + Guid.NewGuid().ToString("N"));

    public ImportCliTests()
    {
        Directory.CreateDirectory(_dir);
        using var db = NewContext();
        db.Database.EnsureDeleted();
        db.Database.Migrate();
    }

    public void Dispose()
    {
        using (var db = NewContext()) db.Database.EnsureDeleted();
        if (Directory.Exists(_dir)) Directory.Delete(_dir, true);
    }

    private static JobTrackerDbContext NewContext() =>
        new(new DbContextOptionsBuilder<JobTrackerDbContext>().UseSqlServer(TestConnection).Options);

    private string Csv(params string[] rows)
    {
        var path = Path.Combine(_dir, "clean_rows.csv");
        File.WriteAllText(path, Header + "\n" + string.Join("\n", rows) + "\n");
        return path;
    }

    private static (int Code, string Out, string Err) Run(params string[] args)
    {
        var o = new StringWriter();
        var e = new StringWriter();
        var code = ImportCli.Run(args, o, e);
        return (code, o.ToString(), e.ToString());
    }

    private string BackupDir => Path.Combine(_dir, "backups");

    [Fact]
    public void The_default_is_a_dry_run_that_writes_nothing_and_takes_no_backup()
    {
        var (code, output, _) = Run("--csv", Csv(Applied, Rejected), "--connection", TestConnection, "--backup-dir", BackupDir);

        Assert.Equal(0, code);
        Assert.Contains("Dry run", output);
        Assert.Contains("2 would be imported", output);
        using var db = NewContext();
        Assert.Equal(0, db.Applications.Count());
        Assert.False(Directory.Exists(BackupDir));
    }

    [Fact]
    public void Apply_takes_a_backup_first_then_writes()
    {
        var (code, output, _) = Run("--csv", Csv(Applied, Rejected), "--connection", TestConnection,
            "--backup-dir", BackupDir, "--apply");

        Assert.Equal(0, code);
        Assert.Contains("2 imported", output);
        Assert.Single(Directory.GetFiles(BackupDir, "*.bak"));
        using var db = NewContext();
        Assert.Equal(2, db.Applications.Count());
    }

    [Fact]
    public void Apply_on_the_real_database_without_the_flag_is_refused_with_exit_code_2()
    {
        var (code, _, err) = Run("--csv", Csv(Applied), "--connection", RealConnection, "--backup-dir", BackupDir, "--apply");

        Assert.Equal(2, code);
        Assert.Contains("--allow-real-db", err);
        Assert.False(Directory.Exists(BackupDir));
    }

    [Fact]
    public void If_the_backup_fails_nothing_is_written()
    {
        var blocker = Path.Combine(_dir, "not-a-folder");
        File.WriteAllText(blocker, "x"); // a file where the backup folder should be

        var (code, _, err) = Run("--csv", Csv(Applied), "--connection", TestConnection, "--backup-dir", blocker, "--apply");

        Assert.Equal(3, code);
        Assert.Contains("Backup failed", err);
        using var db = NewContext();
        Assert.Equal(0, db.Applications.Count());
    }

    [Fact]
    public void The_report_lists_skipped_rows_by_number_and_never_prints_company_names()
    {
        var (_, output, _) = Run("--csv", Csv(Applied, NoDate), "--connection", TestConnection);

        Assert.Contains("1 skipped", output);
        Assert.Contains("Row 3", output);
        Assert.DoesNotContain("Acme", output);
        Assert.DoesNotContain("Gamma", output);
    }

    [Fact]
    public void A_missing_csv_file_is_a_usage_error()
    {
        var (code, _, err) = Run("--csv", Path.Combine(_dir, "nope.csv"), "--connection", TestConnection);

        Assert.Equal(1, code);
        Assert.Contains("not found", err);
    }

    [Fact]
    public void No_csv_option_is_a_usage_error()
    {
        var (code, _, err) = Run("--connection", TestConnection);

        Assert.Equal(1, code);
        Assert.Contains("--csv", err);
    }

    [Fact]
    public void A_file_with_the_wrong_header_fails_with_exit_code_3_and_writes_nothing()
    {
        var bad = Path.Combine(_dir, "bad.csv");
        File.WriteAllText(bad, "a,b,c\n1,2,3\n");

        var (code, _, _) = Run("--csv", bad, "--connection", TestConnection, "--backup-dir", BackupDir, "--apply");

        Assert.Equal(3, code);
        using var db = NewContext();
        Assert.Equal(0, db.Applications.Count());
    }
}
