using Microsoft.Data.SqlClient;

namespace JobTracker.Data.Seeding;

public sealed class DemoGuardException(string message) : Exception(message);

/// <summary>
/// Two-lane privacy, enforced by code: synthetic seeding and snapshot export may only ever touch the
/// LocalDB database named exactly JobTracker_Demo. The real JobTracker database is refused.
/// </summary>
public static class DemoDatabaseGuard
{
    public const string DemoDatabase = "JobTracker_Demo";

    /// <param name="allowedDatabase">Only tests pass anything but the default, for their throwaway databases.</param>
    public static void Require(string? connectionString, string allowedDatabase = DemoDatabase)
    {
        if (string.IsNullOrWhiteSpace(connectionString))
            throw new DemoGuardException("Refused: no connection string.");

        SqlConnectionStringBuilder builder;
        try
        {
            builder = new SqlConnectionStringBuilder(connectionString);
        }
        catch (ArgumentException)
        {
            throw new DemoGuardException("Refused: the connection string could not be read.");
        }

        if (!builder.DataSource.StartsWith(@"(localdb)\", StringComparison.OrdinalIgnoreCase))
            throw new DemoGuardException("Refused: only a LocalDB server on this machine is allowed.");

        if (!string.IsNullOrEmpty(builder.AttachDBFilename))
            throw new DemoGuardException("Refused: attaching a database file is not allowed.");

        if (!string.Equals(builder.InitialCatalog, allowedDatabase, StringComparison.Ordinal))
        {
            var shown = string.IsNullOrEmpty(builder.InitialCatalog) ? "(none)" : builder.InitialCatalog;
            throw new DemoGuardException(
                $"Refused: database '{shown}' is not '{allowedDatabase}'. Synthetic data tools never touch other databases.");
        }
    }
}
