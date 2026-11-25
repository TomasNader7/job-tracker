using Microsoft.Data.SqlClient;

namespace JobTracker.Data.Importing;

public sealed class ImportGuardException(string message) : Exception(message);

/// <summary>
/// Checks the connection string before the import command opens any database.
/// A dry run may read the real JobTracker database. A real write to it needs an explicit flag.
/// </summary>
public static class ImportDatabaseGuard
{
    public const string RealDatabase = "JobTracker";

    public static void Require(string? connectionString, bool write, bool allowRealDb)
    {
        if (string.IsNullOrWhiteSpace(connectionString))
            throw new ImportGuardException("Refused: no connection string.");

        SqlConnectionStringBuilder builder;
        try
        {
            builder = new SqlConnectionStringBuilder(connectionString);
        }
        catch (ArgumentException)
        {
            throw new ImportGuardException("Refused: the connection string could not be read.");
        }

        if (!builder.DataSource.StartsWith(@"(localdb)\", StringComparison.OrdinalIgnoreCase))
            throw new ImportGuardException("Refused: only a LocalDB server on this machine is allowed.");

        if (!string.IsNullOrEmpty(builder.AttachDBFilename))
            throw new ImportGuardException("Refused: attaching a database file is not allowed.");

        if (string.IsNullOrEmpty(builder.InitialCatalog))
            throw new ImportGuardException("Refused: the connection string must name a database.");

        // Case-insensitive on purpose: SQL Server treats "jobtracker" and "JobTracker" as the same database.
        var isReal = string.Equals(builder.InitialCatalog, RealDatabase, StringComparison.OrdinalIgnoreCase);
        if (write && isReal && !allowRealDb)
            throw new ImportGuardException(
                $"Refused: writing to the real '{RealDatabase}' database needs the explicit --allow-real-db flag. Run a dry run first.");
    }
}
