# Mobile AI Assistant Gateway Blueprints

## Android / Gemini Intent Mapping

```xml
<!-- res/xml/shortcuts.xml -->
<shortcuts xmlns:android="http://schemas.android.com/apk/res/android">
  <shortcut
      android:shortcutId="assistant_query"
      android:shortcutShortLabel="Ask Assistant"
      android:shortcutLongLabel="Ask the business assistant about visits, alerts, or orders">
    <intent
        android:action="android.intent.action.VIEW"
        android:targetPackage="com.example.businessassistant"
        android:targetClass="com.example.businessassistant.AssistantActivity" />
  </shortcut>
</shortcuts>
```

```json
{
  "intent": "assistant_query",
  "parameters": {
    "query": "string",
    "entity_name": "string",
    "date": "string",
    "product_category": "string"
  }
}
```

Suggested intent parser logic:
- Extract `entity_name` from phrases like “to Prince Enterprises” or “for Alpha Traders”.
- Extract `date` from words like “today”, “yesterday”, “this month”, or explicit ISO dates.
- Extract `product_category` from product or category phrases such as “towels” or “design”.

## iOS / Siri App Intents Blueprint

```swift
import AppIntents

struct AssistantQueryIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask Business Assistant"
    static var description = IntentDescription("Query your distributor, scheduling, and alert data")

    @Parameter(title: "Query") var query: String

    func perform() async throws -> some ProvidesDialog {
        let response = try await AssistantGateway.shared.query(query: query)
        return .result(dialog: "\(response)")
    }
}
```

Suggested Siri response format:
- “You last visited Prince Enterprises 5 days ago. They have an outstanding of ₹40,000.”
- “There are 2 active alerts for today’s invoice review.”

## Offline + Sync Behavior
- Prefer local SQLite when the device is offline.
- Queue AI requests through the existing offline sync layer when connectivity is unavailable.
- Sync results through Firebase when the connection resumes.
