using JobTracker.Data;
using JobTracker.Data.Importing;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Own throwaway database (JobTracker_Test_Backup). Backups go to a temp folder and are deleted after.</summary>
public sealed class DatabaseBackupTests : IDisposable
{
    private const string Conn = @"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Backup;Trusted_Connection=True;TrustServerCertificate=True;";
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "jt-backup-" + Guid.NewGuid().ToString("N"));
    private static readonly DateTimeOffset Now = new(2026, 11, 25, 14, 30, 5, TimeSpan.Zero);

    public DatabaseBackupTests()
    {
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
        new(new DbContextOptionsBuilder<JobTrackerDbContext>().UseSqlServer(Conn).Options);

    [Fact]
    public void Creates_a_non_empty_backup_file_named_with_the_database_and_time()
    {
        var path = DatabaseBackup.Create(Conn, _dir, Now);

        Assert.True(File.Exists(path));
        Assert.True(new FileInfo(path).Length > 0);
        Assert.Equal("JobTracker_Test_Backup_20261125T143005Z.bak", Path.GetFileName(path));
        Assert.Equal(_dir, Path.GetDirectoryName(path));
    }

    [Fact]
    public void Creates_the_backup_folder_if_it_is_missing()
    {
        Assert.False(Directory.Exists(_dir));

        DatabaseBackup.Create(Conn, _dir, Now);

        Assert.True(Directory.Exists(_dir));
    }

    [Fact]
    public void Refuses_to_overwrite_an_existing_backup()
    {
        DatabaseBackup.Create(Conn, _dir, Now);

        Assert.Throws<IOException>(() => DatabaseBackup.Create(Conn, _dir, Now));
    }

    [Fact]
    public void A_database_that_does_not_exist_fails_and_leaves_no_file()
    {
        var missing = Conn.Replace("JobTracker_Test_Backup", "JobTracker_Test_Backup_Missing");

        Assert.ThrowsAny<Exception>(() => DatabaseBackup.Create(missing, _dir, Now));

        Assert.Empty(Directory.Exists(_dir) ? Directory.GetFiles(_dir) : []);
    }
}
