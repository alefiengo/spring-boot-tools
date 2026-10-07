package example.orders;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {
    private final OrderRepository orders;

    public OrderService(OrderRepository orders) {
        this.orders = orders;
    }

    @Transactional
    public void approve(long orderId, String currentUserId, String eventId) {
        Order order = orders.find(orderId);
        if (!order.ownerId().equals(currentUserId)) {
            throw new OwnershipDenied();
        }
        orders.markApproved(orderId);
        // The fixture's FAIL_DB event violates a real database constraint.
        orders.appendEvent(orderId, eventId);
    }

    public record Order(long id, String ownerId, String status) {}
    public static class OwnershipDenied extends RuntimeException {}
}
