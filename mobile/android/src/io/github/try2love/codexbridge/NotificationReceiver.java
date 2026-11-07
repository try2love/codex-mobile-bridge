package io.github.try2love.codexbridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.webkit.CookieManager;
import java.io.IOException;
import java.util.*;
import java.util.concurrent.*;
import org.json.*;

/** One serial inbox reader per computer; lifecycle changes retire in-flight reads. */
final class NotificationReceiver implements AutoCloseable {
  enum Status { RECEIVING, RETRYING, SIGN_IN_REQUIRED }

  private static final class Computer {
    final String origin, binding;
    final long sourceRevision;
    final boolean notificationsEnabled;
    NotificationCheckpoint checkpoint;
    EventClient.Read read;
    ScheduledFuture<?> next;
    int failures;
    String revision;
    boolean reconnect;
    Status status = Status.RETRYING;

    Computer(String origin, String binding, NotificationCheckpoint checkpoint, SharedPreferences prefs) {
      this.origin = origin;
      this.binding = binding;
      this.checkpoint = checkpoint;
      sourceRevision = NotificationSources.revision(prefs, origin);
      notificationsEnabled = NotificationSources.enabled(prefs, origin);
    }
  }

  private final Context context;
  private final SharedPreferences prefs;
  private final Runnable onChange;
  private final ScheduledThreadPoolExecutor workers = new ScheduledThreadPoolExecutor(1);
  private final Map<String, Computer> computers = new HashMap<>();
  private boolean closed;

  NotificationReceiver(Context context, Runnable onChange) {
    this.context = context;
    this.onChange = onChange;
    prefs = context.getSharedPreferences("bridge", 0);
    workers.setRemoveOnCancelPolicy(true);
  }

  synchronized void configure(boolean active, boolean resume) {
    if (closed) return;
    Map<String, String> bindings = new HashMap<>();
    if (active) for (String origin : new HashSet<>(prefs.getStringSet("origins", Collections.emptySet()))) {
      if(!GatewayChannels.enabled(prefs,origin,origin))continue;
      EventClient.setName(origin, prefs.getString("cookieName:" + origin, null));
      String binding = binding(origin);
      if (!binding.isEmpty()) bindings.put(origin, binding);
    }
    workers.setCorePoolSize(Math.max(1, bindings.size()));
    Iterator<Computer> iterator = computers.values().iterator();
    while (iterator.hasNext()) {
      Computer computer = iterator.next();
      if (!computer.binding.equals(bindings.get(computer.origin))
          || computer.sourceRevision != NotificationSources.revision(prefs, computer.origin)) {
        iterator.remove();
        retire(computer);
      }
    }
    for (Map.Entry<String, String> row : bindings.entrySet()) if (!computers.containsKey(row.getKey())) {
      NotificationCheckpoint checkpoint = resume
          ? NotificationCheckpoint.restore(prefs.getString("notificationCheckpoint:" + row.getKey(), null), row.getValue())
          : NotificationCheckpoint.empty();
      Computer computer = new Computer(row.getKey(), row.getValue(), checkpoint, prefs);
      computers.put(row.getKey(), computer);
      schedule(computer, 0);
    }
    onChange.run();
  }

  synchronized Status status() {
    if (computers.isEmpty()) return Status.SIGN_IN_REQUIRED;
    boolean retrying = false;
    for (Computer computer : computers.values()) {
      if (computer.status == Status.RECEIVING) return Status.RECEIVING;
      retrying |= computer.status == Status.RETRYING;
    }
    return retrying ? Status.RETRYING : Status.SIGN_IN_REQUIRED;
  }

  synchronized Map<String,Status> connectionStates() {
    Map<String,Status> result=new TreeMap<>();
    for(Computer computer:computers.values())result.put(computer.origin,computer.status);
    return result;
  }

  private void updateStatus(Computer computer, Status status) {
    if (computer.status == status) return;
    computer.status = status;
    android.util.Log.i("BridgeNotifications", "Inbox status host=" + GatewayURL.httpOrigin(computer.origin) + " state=" + status);
    onChange.run();
  }

  synchronized void refresh() {
    for (Computer computer : computers.values()) if (computer.read == null) schedule(computer, 0);
  }

  synchronized void reconnect() {
    for (Computer computer : computers.values()) {
      if (computer.read == null) schedule(computer, 0);
      else { computer.reconnect = true; computer.read.cancel(); }
    }
  }

  private void schedule(Computer computer, long seconds) {
    if (computer.next != null) computer.next.cancel(false);
    computer.next = workers.schedule(() -> check(computer), seconds, TimeUnit.SECONDS);
  }

  private boolean current(Computer computer) {
    return !closed && computers.get(computer.origin) == computer
        && GatewayChannels.enabled(prefs,computer.origin,computer.origin)
        && computer.sourceRevision == NotificationSources.revision(prefs, computer.origin)
        && prefs.getStringSet("origins", Collections.emptySet()).contains(computer.origin)
        && computer.binding.equals(binding(computer.origin));
  }

  private static String binding(String origin) {
    return EventClient.tokenValue(EventClient.sessionCookie(CookieManager.getInstance().getCookie(origin + "/"), origin));
  }

  private void check(Computer computer) {
    EventClient.Read read = new EventClient.Read();
    long started = System.nanoTime();
    synchronized (this) {
      if (!current(computer) || computer.read != null) return;
      computer.read = read;
    }
    try {
      JSONObject data = EventClient.read(computer.origin, computer.checkpoint.cursor, read,
          computer.checkpoint.stream, computer.revision);
      long received = System.nanoTime();
      long requestMs = TimeUnit.NANOSECONDS.toMillis(received - started);
      JSONObject timing = data.optJSONObject("timing");
      long waitMs = timing == null ? -1 : timing.optLong("waitMs", -1);
      android.util.Log.i("BridgeNotifications", "Inbox timing host=" + GatewayURL.httpOrigin(computer.origin)
          + " requestMs=" + requestMs + " serverWaitMs=" + waitMs
          + " overheadMs=" + (waitMs < 0 ? -1 : Math.max(0, requestMs - waitMs)));
      NotificationCheckpoint checkpoint = NotificationCheckpoint.read(data);
      synchronized (this) {
        // Removal and Cookie renewal share EventClient's binding lock.
        synchronized (EventClient.class) {
          if (!current(computer)) return;
          ComputerNotifications.update(computer.origin, EventClient.sessionCookie(
              CookieManager.getInstance().getCookie(computer.origin + "/"), computer.origin), data);
        }
      }
      if (data.optBoolean("enabled")) receive(computer, checkpoint, data, received);
      synchronized (this) {
        if (current(computer)) {
          computer.failures = 0;
          String revision = data.optString("revision", "");
          computer.revision = data.optBoolean("enabled") && data.optInt("longPoll", 0) == EventClient.POLL_SECONDS
              && revision.matches("[a-f0-9]{32}") ? revision : null;
          updateStatus(computer, data.optBoolean("enabled") ? Status.RECEIVING : Status.RETRYING);
        }
      }
    } catch (Exception unavailable) {
      synchronized (this) {
        if (current(computer) && !computer.reconnect) {
          computer.revision = null;
          computer.failures = Math.min(4, computer.failures + 1);
          updateStatus(computer, unavailable instanceof EventClient.AuthenticationRequired
              ? Status.SIGN_IN_REQUIRED : Status.RETRYING);
          // Log the endpoint and code location while keeping credentials and notification content private.
          StackTraceElement[] trace = unavailable.getStackTrace();
          android.util.Log.w("BridgeNotifications", "Inbox failure host=" + GatewayURL.httpOrigin(computer.origin)
              + " requestMs=" + TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)
              + " type=" + unavailable.getClass().getSimpleName()
              + (unavailable instanceof EventClient.HttpFailure ? " http=" + ((EventClient.HttpFailure) unavailable).status : "")
              + " at=" + (trace.length == 0 ? "unknown" : trace[0]));
          ComputerNotifications.forget(computer.origin);
        }
      }
    } finally {
      synchronized (this) {
        computer.read = null;
        if (current(computer)) {
          long delay = computer.reconnect || (computer.failures == 0 && computer.revision != null)
              ? 0 : Math.min(120, 15L << computer.failures);
          computer.reconnect = false;
          schedule(computer, delay);
        }
      }
    }
  }

  private void receive(Computer computer, NotificationCheckpoint checkpoint, JSONObject data, long received) throws Exception {
    if (checkpoint.stream.equals(computer.checkpoint.stream) && checkpoint.cursor < computer.checkpoint.cursor)
      throw new IOException("Inbox cursor moved backwards");
    if (checkpoint.follows(computer.checkpoint)) {
      JSONArray events = data.getJSONArray("events");
      long previous = computer.checkpoint.cursor;
      long cleared = prefs.getLong("cleared:" + computer.origin + ":" + checkpoint.stream, 0);
      for (int i = 0; i < events.length(); i++) {
        JSONObject event = events.getJSONObject(i);
        long sequence = event.getLong("sequence");
        if (sequence <= previous || sequence > checkpoint.cursor) throw new IOException("Invalid inbox order");
        previous = sequence;
        synchronized (this) {
          synchronized (EventClient.class) {
            if (!current(computer)) return;
            if (sequence > cleared && (!event.has("unread") || event.optBoolean("unread"))
                && computer.notificationsEnabled && prefs.getBoolean("alerts", false)) {
              TaskNotifications.show(context, computer.origin, checkpoint.stream, event, "foreground-tasks");
              String trace = event.optString("trace", "");
              android.util.Log.i("BridgeNotifications", "Inbox posted host=" + GatewayURL.httpOrigin(computer.origin)
                  + " sequence=" + sequence + " trace=" + (trace.matches("[a-f0-9]{16}") ? trace : "unknown")
                  + " serverAgeMs=" + event.optLong("ageMs", -1)
                  + " receiveToPostMs=" + TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - received));
            }
            save(computer, new NotificationCheckpoint(checkpoint.stream, sequence));
          }
        }
      }
    }
    // First connection and a replaced stream establish a baseline instead of replaying history.
    synchronized (this) {
      synchronized (EventClient.class) { if (current(computer)) save(computer, checkpoint); }
    }
  }

  private void save(Computer computer, NotificationCheckpoint checkpoint) throws Exception {
    if (checkpoint.stream.equals(computer.checkpoint.stream) && checkpoint.cursor == computer.checkpoint.cursor) return;
    if (!prefs.edit().putString("notificationCheckpoint:" + computer.origin, checkpoint.encode(computer.binding)).commit())
      throw new IOException("Could not save inbox checkpoint");
    computer.checkpoint = checkpoint;
  }

  private void retire(Computer computer) {
    if (computer.next != null) computer.next.cancel(false);
    if (computer.read != null) computer.read.cancel();
  }

  public synchronized void close() {
    closed = true;
    for (Computer computer : computers.values()) retire(computer);
    computers.clear();
    workers.shutdownNow();
  }
}
