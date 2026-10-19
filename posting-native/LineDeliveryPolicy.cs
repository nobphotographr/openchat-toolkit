using System;
using System.Globalization;

// Pure clock policy, shared with offline boundary tests. No desktop interaction.
public static class LineDeliveryPolicy {
    public static bool InWindow(DateTimeOffset utcNow) {
        int hour = utcNow.ToOffset(TimeSpan.FromHours(9)).Hour;
        return hour >= 8 && hour < 21;
    }
    public static bool ValidDates(string businessDate, string deliveryDate, DateTimeOffset utcNow) {
        DateTime business;
        if (!DateTime.TryParseExact(businessDate,"yyyy-MM-dd",CultureInfo.InvariantCulture,DateTimeStyles.None,out business)) return false;
        DateTime today = utcNow.ToOffset(TimeSpan.FromHours(9)).Date;
        return deliveryDate == today.ToString("yyyy-MM-dd") && business <= today && business >= today.AddDays(-1);
    }
}
