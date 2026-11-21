using System.Text.Json;
using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

/// <summary>
/// Runs the cross-language golden fixture (tests/contract/projection_cases_v1.json) against the C# projection.
/// The Databricks notebook runs the same cases, so both runtimes are held to one definition.
/// </summary>
public class ProjectionContractTests
{
    private sealed record FixtureEvent(long EventSequence, int ApplicationId, string? PreviousStatus, string NewStatus,
        DateTimeOffset EffectiveAt, DateTimeOffset RecordedAt, string Source, long? SupersedesEventSequence, string? Note);

    private sealed record FixtureExpected(string? Status, int IsNoReply, List<string> ProblemCodes, string? InterviewDate);

    private sealed record FixtureCase(string Name, List<FixtureEvent> Events, Dictionary<string, FixtureExpected> Expected);

    private sealed record Fixture(string ContractVersion, DateTimeOffset AsOf, List<FixtureCase> Cases);

    private static readonly Fixture Loaded = Load();

    private static Fixture Load()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "contract", "projection_cases_v1.json");
        var options = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
        return JsonSerializer.Deserialize<Fixture>(File.ReadAllText(path), options)!;
    }

    public static IEnumerable<object[]> CaseNames() => Loaded.Cases.Select(c => new object[] { c.Name });

    private static StatusEvent ToDomain(FixtureEvent e) => StatusEvent.Create(
        e.EventSequence, e.ApplicationId,
        e.PreviousStatus is null ? null : Enum.Parse<ApplicationStatus>(e.PreviousStatus),
        Enum.Parse<ApplicationStatus>(e.NewStatus), e.EffectiveAt, e.RecordedAt,
        Enum.Parse<EventSource>(e.Source), e.SupersedesEventSequence, e.Note);

    [Fact]
    public void Fixture_is_contract_version_1_and_not_empty()
    {
        Assert.Equal("1", Loaded.ContractVersion);
        Assert.True(Loaded.Cases.Count >= 19);
    }

    [Theory]
    [MemberData(nameof(CaseNames))]
    public void Projection_matches_the_golden_case(string name)
    {
        var fixtureCase = Loaded.Cases.Single(c => c.Name == name);

        foreach (var group in fixtureCase.Events.GroupBy(e => e.ApplicationId))
        {
            var expected = fixtureCase.Expected[group.Key.ToString()];
            var result = StatusProjection.Project(group.Select(ToDomain), Loaded.AsOf);

            Assert.Equal(expected.Status, result.Status?.ToString());
            Assert.Equal(expected.IsNoReply == 1, result.IsNoReply);
            Assert.Equal(expected.ProblemCodes.Order(), result.ProblemDetails.Select(p => p.ContractCode).Order());
            Assert.Equal(result.ProblemDetails.Count, result.Problems.Count);
            Assert.Equal(expected.InterviewDate,
                result.FirstInterviewedAt?.UtcDateTime.ToString("yyyy-MM-dd"));
        }
    }
}
