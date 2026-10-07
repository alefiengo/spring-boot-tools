package example.orders;

public final class FixtureData {
    public static final long ORDER_ID = 101;
    public static final String OWNER = "alice";
    public static final String OTHER_USER = "bob";
    public static final String SUCCESS_EVENT = "approved-101";
    public static final String FAILING_EVENT = "FAIL_DB";

    private FixtureData() {}

    public static OrderService.Order pendingOrder() {
        return new OrderService.Order(ORDER_ID, OWNER, "PENDING");
    }
}
