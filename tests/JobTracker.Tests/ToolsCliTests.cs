using JobTracker.Data;
using JobTracker.Tools;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Uses the throwaway database JobTracker_Test_Cli. Never touches JobTracker or JobTracker_Demo.</summary>
public sealed class ToolsCliTests : IDisposable
{
    private const string Db = "JobTracker_Test_Cli";
    private const string RealConnection = @"Server=(localdb)\MSSQLLocalDB;Database=JobTracker;Trusted_Connection=True;TrustServerCertificate=True;";
    private static readonly string TestConnection = $@"Server=(localdb)\MSSQLLocalDB;Database={Db};Trusted_Connection=True;TrustServerCertificate=True;";
    private readonly List<string> _tempDirs = [];

    public void Dispose()
    {
        using (var db = new JobTrackerDbContext(new DbContextOptionsBuilder<JobTrackerDbContext>().UseSqlServer(TestConnection).Options))
            db.Database.EnsureDeleted();
        foreach (var d in _tempDirs.Where(Directory.Exists)) Directory.Delete(d, true);
    }

    private static (int Code, string Out, string Err) Run(string allowed, params string[] args)
    {
        var o = new StringWriter();
        var e = new StringWriter();
        var code = ToolsCli.Run(args, o, e, allowed);
        return (code, o.ToString(), e.ToString());
    }

    private string TempDir()
    {
        var d = Path.Combine(Path.GetTempPath(), "jt-cli-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(d);
        _tempDirs.Add(d);
        return d;
    }

    [Theory]
    [InlineData("seed-demo")]
    [InlineData("export-snapshot")]
    public void The_real_database_is_refused_with_exit_code_2_before_anything_is_opened(string command)
    {
        var (code, _, err) = Run("JobTracker_Demo", command, "--connection", RealConnection);

        Assert.Equal(2, code);
        Assert.Contains("Refused", err);
    }

    [Fact]
    public void The_public_entry_point_allows_only_the_demo_database()
    {
        var e = new StringWriter();

        var code = ToolsCli.Run(["seed-demo", "--connection", TestConnection, "--reset"], new StringWriter(), e);

        Assert.Equal(2, code);
        Assert.Contains("Refused", e.ToString());
    }

    [Theory]
    [InlineData]
    [InlineData("nonsense")]
    [InlineData("seed-demo", "--bogus", "x")]
    [InlineData("export-snapshot", "--snapshot-id")]
    public void Unknown_commands_and_arguments_give_exit_code_1(params string[] args)
    {
        var (code, _, err) = Run(Db, args);

        Assert.Equal(1, code);
        Assert.NotEmpty(err);
    }

    [Fact]
    public void Seed_then_export_works_on_an_allowed_database()
    {
        var repo = RepoPaths.SyntheticDataDir;
        var seed = Run(Db, "seed-demo", "--connection", TestConnection, "--reset", "--csv-dir", repo, "--as-of", "2026-11-16");
        Assert.True(seed.Code == 0, seed.Err + seed.Out);
        Assert.Contains("182 applications", seed.Out);
        Assert.Contains("0 projection problems", seed.Out);

        var outDir = TempDir();
        var export = Run(Db, "export-snapshot", "--connection", TestConnection, "--out", outDir,
            "--as-of", "2026-11-16T00:00:00Z", "--exported-at", "2026-11-17T09:30:00Z");

        Assert.True(export.Code == 0, export.Err + export.Out);
        var folder = Assert.Single(Directory.GetDirectories(outDir));
        Assert.Contains(Path.GetFileName(folder), export.Out);
        Assert.Equal(3, Directory.GetFiles(folder).Length);
    }

    [Fact]
    public void Seeding_a_second_time_without_reset_is_refused_and_leaves_the_data_alone()
    {
        var csv = RepoPaths.SyntheticDataDir;
        Assert.Equal(0, Run(Db, "seed-demo", "--connection", TestConnection, "--reset", "--csv-dir", csv).Code);

        var again = Run(Db, "seed-demo", "--connection", TestConnection, "--csv-dir", csv);

        Assert.Equal(3, again.Code);
        Assert.Contains("not empty", again.Err);
        using var db = new JobTrackerDbContext(new DbContextOptionsBuilder<JobTrackerDbContext>().UseSqlServer(TestConnection).Options);
        Assert.Equal(182, db.Applications.Count());
    }

    [Fact]
    public void A_bad_as_of_is_a_usage_error()
    {
        var (code, _, err) = Run(Db, "export-snapshot", "--connection", TestConnection, "--as-of", "yesterday");

        Assert.Equal(1, code);
        Assert.Contains("as-of", err);
    }
}
