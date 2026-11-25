using JobTracker.Data.Importing;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Pure checks on connection strings. No database is opened.</summary>
public sealed class ImportDatabaseGuardTests
{
    private static string Conn(string db, string server = @"(localdb)\MSSQLLocalDB", string extra = "") =>
        $"Server={server};Database={db};Trusted_Connection=True;TrustServerCertificate=True;{extra}";

    [Fact]
    public void A_dry_run_on_the_real_database_is_allowed_without_any_flag()
    {
        ImportDatabaseGuard.Require(Conn("JobTracker"), write: false, allowRealDb: false);
    }

    [Fact]
    public void A_real_write_to_the_real_database_is_refused_without_the_flag()
    {
        var ex = Assert.Throws<ImportGuardException>(() =>
            ImportDatabaseGuard.Require(Conn("JobTracker"), write: true, allowRealDb: false));

        Assert.Contains("--allow-real-db", ex.Message);
    }

    [Fact]
    public void A_real_write_to_the_real_database_is_allowed_with_the_flag()
    {
        ImportDatabaseGuard.Require(Conn("JobTracker"), write: true, allowRealDb: true);
    }

    [Fact]
    public void A_write_to_a_throwaway_database_needs_no_flag()
    {
        ImportDatabaseGuard.Require(Conn("JobTracker_Test_Import"), write: true, allowRealDb: false);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void A_missing_connection_string_is_refused(string text)
    {
        Assert.Throws<ImportGuardException>(() => ImportDatabaseGuard.Require(text, write: false, allowRealDb: false));
    }

    [Fact]
    public void A_server_that_is_not_local_db_is_refused_even_with_the_flag()
    {
        Assert.Throws<ImportGuardException>(() =>
            ImportDatabaseGuard.Require(Conn("JobTracker", server: "prod-sql.example.com"), write: true, allowRealDb: true));
    }

    [Fact]
    public void Attaching_a_database_file_is_refused()
    {
        Assert.Throws<ImportGuardException>(() =>
            ImportDatabaseGuard.Require(Conn("JobTracker", extra: @"AttachDbFilename=C:\x.mdf;"), write: false, allowRealDb: false));
    }

    [Fact]
    public void A_connection_string_with_no_database_name_is_refused()
    {
        Assert.Throws<ImportGuardException>(() =>
            ImportDatabaseGuard.Require(@"Server=(localdb)\MSSQLLocalDB;Trusted_Connection=True;", write: false, allowRealDb: false));
    }

    [Fact]
    public void The_real_database_name_must_match_exactly_so_a_case_trick_cannot_skip_the_flag()
    {
        // SQL Server treats these as the same database, so a differently-cased name still needs the flag.
        Assert.Throws<ImportGuardException>(() =>
            ImportDatabaseGuard.Require(Conn("jobtracker"), write: true, allowRealDb: false));
    }
}
