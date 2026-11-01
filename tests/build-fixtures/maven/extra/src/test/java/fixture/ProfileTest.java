package fixture;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
class ProfileTest {
    @Test void profileActivated() { assertEquals("enabled", System.getProperty("fixture.profile")); }
}
