package example.orders;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {
    private final JdbcTemplate jdbc;

    public OrderRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public OrderService.Order find(long id) {
        return jdbc.queryForObject(
            "select id, owner_id, status from orders where id = ?",
            (row, index) -> new OrderService.Order(
                row.getLong("id"), row.getString("owner_id"), row.getString("status")), id);
    }

    public void markApproved(long id) {
        jdbc.update("update orders set status = 'APPROVED' where id = ?", id);
    }

    public void appendEvent(long orderId, String eventId) {
        jdbc.update("insert into order_events(order_id, event_id) values (?, ?)", orderId, eventId);
    }
}
