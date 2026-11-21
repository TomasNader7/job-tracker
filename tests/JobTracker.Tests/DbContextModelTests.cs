using JobTracker.Data;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

public class DbContextModelTests
{
    // Builds the model only. No connection is opened, so no database is needed.
    private static JobTrackerDbContext NewContext() =>
        new(new DbContextOptionsBuilder<JobTrackerDbContext>()
            .UseSqlServer("Server=(localdb)\\MSSQLLocalDB;Database=ModelOnly;Trusted_Connection=True;")
            .Options);

    [Fact]
    public void Model_has_a_table_for_applications_and_one_for_status_events()
    {
        using var db = NewContext();

        Assert.NotNull(db.Model.FindEntityType(typeof(Application)));
        Assert.NotNull(db.Model.FindEntityType(typeof(StatusEvent)));
    }
}
