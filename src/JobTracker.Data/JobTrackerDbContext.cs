using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Data;

/// <summary>
/// The "clerk's desk": lists which drawers (tables) exist and how each form maps to a row.
/// It does not hold any business rules.
/// </summary>
public sealed class JobTrackerDbContext(DbContextOptions<JobTrackerDbContext> options) : DbContext(options)
{
    public DbSet<Application> Applications => Set<Application>();
    public DbSet<StatusEvent> StatusEvents => Set<StatusEvent>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<StatusEvent>(e =>
        {
            // StatusEvent has read-only properties (it is never edited), so EF needs to be told
            // to map each one. It fills them in through the private constructor.
            e.HasKey(x => x.Id);
            e.Property(x => x.Id).ValueGeneratedOnAdd();
            e.Property(x => x.ApplicationId);
            e.Property(x => x.PreviousStatus);
            e.Property(x => x.NewStatus);
            e.Property(x => x.EffectiveAt);
            e.Property(x => x.RecordedAt);
            e.Property(x => x.Source);
            e.Property(x => x.SupersedesEventId);
            e.Property(x => x.Note);
        });
    }
}
