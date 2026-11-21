using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace JobTracker.Data;

/// <summary>
/// Only used by the `dotnet ef` command-line tool. It tells the tool how to build the DbContext
/// when no web app is running. The app itself will get its connection string from appsettings.
/// </summary>
public sealed class DesignTimeDbContextFactory : IDesignTimeDbContextFactory<JobTrackerDbContext>
{
    public JobTrackerDbContext CreateDbContext(string[] args)
    {
        var options = new DbContextOptionsBuilder<JobTrackerDbContext>()
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker;Trusted_Connection=True;")
            .Options;
        return new JobTrackerDbContext(options);
    }
}
