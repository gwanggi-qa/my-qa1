import com.sun.jdi.*;
import com.sun.jdi.connect.*;
import com.sun.jdi.event.*;
import com.sun.jdi.request.*;
import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** One local JDI attempt. The ONLY debuggee write is recognitionFrame's short[].
 * On abort, cancel(true) is allowed only on the recording task owned by this run.
 * No engine setters, score setters, text setters, playback, or adb commands.
 */
public final class SpeechProbe {
    static final String HCI = "com.weaversmind.data.HciControl";
    static final String TASK = HCI + "$HciControlTask";
    static final String SLOT = "com.weaversmind.data.CognitionSlot";
    static final String INPUT = "([SLcom/selvy/sr/EduJPN/SelvySTT_pWrapper;)I";
    static Map<String, Object> newResult() { return map("completed", false, "target_verified", false,
        "full_input", false, "signature_verified", false, "engine_state", null,
        "scores", new LinkedHashMap<String, Object>(), "states", new ArrayList<Object>(),
        "stopped", false, "reason", null); }
    Map<String, Object> result = newResult();
    final List<EventRequest> requests = new ArrayList<EventRequest>();
    VirtualMachine vm;
    ObjectReference hci, task;
    Path root;
    String expected;
    short[] samples;
    byte[] bytes;
    int slot, requestedSlot, offset, frames, readbacks, suppliedFrameSamples;
    long deadline, resultAt, resultThread = -1;
    boolean finished, canceled, suspended, deferredTarget, observationWritten;
    Path controlRoot;
    long overallDeadline;
    int sequenceIndex;
    final List<Trial> sequence = new ArrayList<Trial>();

    static final class Trial {
        Path root;
        String text;
        byte[] bytes;
        short[] samples;
        int slot;
        long timeoutMillis;
        Map<String, Object> report = newResult();
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 7) throw new IllegalArgumentException("mode port directory pcm slot timeoutMs sha256");
        SpeechProbe probe = new SpeechProbe();
        probe.root = Paths.get(args[2]);
        probe.controlRoot = probe.root;
        try {
            probe.run(args);
        } catch (Exception ex) {
            probe.result.put("reason", ex.getClass().getSimpleName() + ": " + ex.getMessage());
            probe.result.put("completed", false);
            probe.result.put("stopped", Files.exists(probe.controlRoot.resolve("stop.signal")));
            ex.printStackTrace(System.out);
        } finally {
            probe.cleanup();
            probe.writeAt(probe.controlRoot, "result.json", probe.exportResult());
        }
        if (!Boolean.TRUE.equals(probe.result.get("completed"))) System.exit(2);
    }

    static Map<String, Object> map(Object... pairs) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        for (int i = 0; i < pairs.length; i += 2) m.put((String)pairs[i], pairs[i + 1]);
        return m;
    }

    static void require(boolean ok, String reason) {
        if (!ok) throw new IllegalStateException(reason);
    }

    static Value field(ObjectReference object, String name) {
        require(object != null, "null_object_for_" + name);
        Field f = object.referenceType().fieldByName(name);
        require(f != null, "missing_field_" + name);
        return object.getValue(f);
    }

    static int integer(Value value) {
        require(value instanceof IntegerValue, "expected_int");
        return ((IntegerValue)value).value();
    }

    static String string(Value value) {
        require(value instanceof StringReference, "expected_string");
        return ((StringReference)value).value();
    }

    ReferenceType type(String name) {
        List<ReferenceType> types = vm.classesByName(name);
        require(types.size() == 1 && types.get(0).isPrepared(), "class_not_uniquely_loaded_" + name);
        return types.get(0);
    }

    static Method method(ReferenceType type, String name, String signature) {
        List<Method> methods = type.methodsByName(name, signature);
        require(methods.size() == 1 && methods.get(0).location() != null, "method_signature_mismatch_" + name);
        return methods.get(0);
    }

    static void signature(ReferenceType type, String name, String signature) {
        Field f = type.fieldByName(name);
        require(f != null && f.signature().equals(signature), "field_signature_mismatch_" + name);
    }

    void breakpoint(Method method) {
        BreakpointRequest request = vm.eventRequestManager().createBreakpointRequest(method.location());
        request.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
        requests.add(request);
        request.enable();
    }

    void check() {
        require(!Files.exists((controlRoot == null ? root : controlRoot).resolve("stop.signal")), "stopped");
        require(System.nanoTime() < deadline, "attempt_timeout");
    }

    static String decoded(String value) {
        return new String(Base64.getDecoder().decode(value), StandardCharsets.UTF_8);
    }

    void loadSequence(Path manifest) throws Exception {
        List<String> lines = Files.readAllLines(manifest, StandardCharsets.UTF_8);
        require(lines.size() == 4 || lines.size() == 8, "complete_four_item_sequence_required");
        for (String line : lines) {
            String[] parts = line.split("\t", -1);
            require(parts.length == 6, "invalid_sequence_manifest");
            Trial trial = new Trial();
            trial.text = decoded(parts[0]);
            require(!trial.text.isEmpty() && trial.text.length() <= 4096, "invalid_sequence_target");
            Path pcm = Paths.get(decoded(parts[1]));
            trial.bytes = Files.readAllBytes(pcm);
            require(trial.bytes.length > 0 && trial.bytes.length <= 480000 && trial.bytes.length % 2 == 0,
                "invalid_sequence_pcm");
            require(hash(trial.bytes).equals(parts[2]), "sequence_input_changed");
            trial.samples = new short[trial.bytes.length / 2];
            ByteBuffer.wrap(trial.bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(trial.samples);
            trial.root = Paths.get(decoded(parts[3]));
            require(Files.isDirectory(trial.root), "sequence_evidence_missing");
            trial.slot = Integer.parseInt(parts[4]);
            trial.timeoutMillis = Long.parseLong(parts[5]);
            require(trial.slot >= -1 && trial.timeoutMillis >= 5000 && trial.timeoutMillis <= 300000,
                "invalid_sequence_deadline_or_slot");
            sequence.add(trial);
        }
        if (sequence.size() == 8) {
            for (int index = 0; index < 8; index += 2) {
                Trial first = sequence.get(index), second = sequence.get(index + 1);
                require(first.slot == 0 && second.slot == 0 && first.text.equals(second.text)
                    && Arrays.equals(first.bytes, second.bytes) && !first.root.equals(second.root),
                    "invalid_word_phase_pair");
            }
        }
        require(sequence.get(0).root.equals(controlRoot), "sequence_root_mismatch");
    }

    void activate(int index) {
        sequenceIndex = index;
        Trial trial = sequence.get(index);
        root = trial.root;
        expected = trial.text;
        bytes = trial.bytes;
        samples = trial.samples;
        requestedSlot = trial.slot;
        result = trial.report;
        result.put("signature_verified", index > 0);
        result.put("sequence_index", index);
        result.put("capture_valid", false);
        offset = frames = readbacks = suppliedFrameSamples = 0;
        resultAt = 0;
        resultThread = -1;
        task = null;
        finished = canceled = observationWritten = false;
        deadline = Math.min(overallDeadline, System.nanoTime() + trial.timeoutMillis * 1000000L);
        System.out.println("ATTEMPT " + index);
    }

    void finishTrial() throws Exception {
        checkpointPng();
        require(finished && offset == samples.length && readbacks == frames && frames > 0, "incomplete_pcm_input");
        require(integerState() != 1 || !((Map<?, ?>)result.get("scores")).isEmpty(), "current_scores_unavailable");
        result.put("completed", true);
        result.put("input", inputEvidence());
        result.put("capture_valid", observationWritten && Files.exists(root.resolve("capture.signal"))
            && !Boolean.TRUE.equals(result.get("capture_raced")));
        write("trial.json", result);
    }

    boolean currentCaptureTarget() {
        return finished && expected.equals(string(field(hci, "recogText")))
            && slot == integer(field(hci, "slotCurrent"))
            && !((BooleanValue)field(hci, "_isRecording")).value();
    }

    void checkpointPng() throws Exception {
        Path signal = root.resolve("png.signal");
        if (!observationWritten || result.containsKey("png_sha256") || !Files.exists(signal)) return;
        String digest = new String(Files.readAllBytes(signal), StandardCharsets.US_ASCII).trim();
        if (!digest.matches("[a-f0-9]{64}")) return; // A partially written signal is retried.
        boolean valid = Boolean.TRUE.equals(result.get("png_start_valid")) && currentCaptureTarget();
        require(Files.size(root.resolve("capture.png")) <= 32 * 1024 * 1024, "capture_too_large");
        require(hash(Files.readAllBytes(root.resolve("capture.png"))).equals(digest), "capture_hash_mismatch");
        result.put("png_sha256", digest);
        result.put("png_capture_valid", valid);
        write("png-checkpoint.json", map("sequence_index", sequenceIndex, "text", expected,
            "slot", slot, "png_sha256", digest, "png_capture_valid", valid));
    }

    Map<String, Object> exportResult() {
        if (sequence.isEmpty()) return result;
        List<Map<String, Object>> reports = new ArrayList<Map<String, Object>>();
        boolean completed = true;
        for (Trial trial : sequence) {
            if (!Boolean.TRUE.equals(trial.report.get("completed"))) {
                completed = false;
                if (trial.report.get("reason") == null) trial.report.put("reason", "sequence_item_not_completed");
            }
            trial.report.put("cleanup", result.get("cleanup"));
            reports.add(trial.report);
        }
        return map("completed", completed, "reason", result.get("reason"), "stopped", result.get("stopped"),
            "cleanup", result.get("cleanup"), "sequence_reports", reports);
    }

    void run(String[] args) throws Exception {
        boolean snapshot = args[0].equals("snapshot");
        boolean queued = args[0].equals("sequence");
        deferredTarget = args[0].equals("transition") || queued;
        require(snapshot || deferredTarget || args[0].equals("inject"), "invalid_mode");
        long millis = Long.parseLong(args[5]);
        require(millis > 0 && millis <= 300000, "invalid_deadline");
        deadline = overallDeadline = System.nanoTime() + millis * 1000000L;
        expected = new String(Files.readAllBytes(root.resolve("target.txt")), StandardCharsets.UTF_8);
        if (queued) {
            loadSequence(Paths.get(args[3]));
            activate(0);
        } else if (!snapshot) {
            require(!expected.isEmpty(), "empty_target");
            bytes = Files.readAllBytes(Paths.get(args[3]));
            require(bytes.length > 0 && bytes.length <= 480000 && bytes.length % 2 == 0, "invalid_pcm_length");
            require(hash(bytes).equals(args[6]), "input_changed_since_preflight");
            samples = new short[bytes.length / 2];
            ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples);
        }
        AttachingConnector connector = null;
        for (AttachingConnector c : Bootstrap.virtualMachineManager().attachingConnectors())
            if (c.name().equals("com.sun.jdi.SocketAttach")) connector = c;
        require(connector != null, "socket_attach_unavailable");
        Map<String, Connector.Argument> options = connector.defaultArguments();
        options.get("hostname").setValue("127.0.0.1");
        options.get("port").setValue(args[1]);
        options.get("timeout").setValue(Long.toString(Math.min(millis, 5000)));
        vm = connector.attach(options);
        check();
        vm.suspend();
        suspended = true;
        ReferenceType hc = type(HCI), tt = type(TASK), st = type(SLOT);
        signature(hc, "gInstance", "Lcom/weaversmind/data/HciControl;");
        signature(hc, "recogText", "Ljava/lang/String;");
        signature(hc, "slotCurrent", "I");
        signature(hc, "m_ariScore", "[[I");
        signature(hc, "m_ariAvgScore", "[I");
        signature(st, "wordn", "I");
        signature(st, "_position", "[I");
        signature(st, "_wordText", "Ljava/lang/String;");
        signature(tt, "this$0", "Lcom/weaversmind/data/HciControl;");
        signature(tt, "RECOG_FRAME_SAMPLES", "I");
        signature(tt, "recogState", "I");
        Method input = method(tt, "recognitionFrame", INPUT);
        Method post = method(tt, "onPostExecute", "(Ljava/lang/Integer;)V");
        Method score = method(st, "setAvgScore", "(I)V");
        // BuildConfig may not be loaded; do not invoke application code to load it.
        List<ReferenceType> configs = vm.classesByName("com.weaversmind.tjp2.BuildConfig");
        if (!configs.isEmpty()) {
            require(configs.size() == 1, "ambiguous_build_config");
            ReferenceType config = configs.get(0);
            require(integer(config.getValue(config.fieldByName("VERSION_CODE"))) == 11, "unsupported_runtime_build");
            require(string(config.getValue(config.fieldByName("APPLICATION_ID"))).equals("com.weaversmind.tjp2"), "unsupported_runtime_package");
        }
        hci = (ObjectReference)hc.getValue(hc.fieldByName("gInstance"));
        require(hci != null, "hci_not_initialized");
        require(!((BooleanValue)field(hci, "_isRecording")).value(), "recording_already_active");
        slot = integer(field(hci, "slotCurrent"));
        Map<String, Object> runtime = snapshot();
        result.put("runtime", runtime);
        result.put("signature_verified", true);
        if (!snapshot) {
            if (!queued) requestedSlot = Integer.parseInt(args[4]);
            if (!deferredTarget) {
                require(expected.equals(string(field(hci, "recogText"))), "target_mismatch_at_attach");
                require(requestedSlot < 0 || requestedSlot == slot, "slot_mismatch_at_attach");
            }
            breakpoint(input);
            breakpoint(post);
            breakpoint(score);
        }
        vm.resume();
        suspended = false;
        write("armed.json", map("signature_verified", true, "runtime", runtime));
        System.out.println("ARMED SETTINGS_UNCHANGED");
        if (snapshot) {
            result.put("completed", true);
            return;
        }
        while (true) {
            check();
            EventSet events = vm.eventQueue().remove(50);
            if (events != null) {
                try {
                    for (Event event : events) {
                        require(!(event instanceof VMDisconnectEvent || event instanceof VMDeathEvent), "vm_disconnected");
                        if (event instanceof BreakpointEvent) handle((BreakpointEvent)event);
                    }
                } finally {
                    events.resume();
                }
            }
            if (finished && System.nanoTime() - resultAt > 150000000L && !observationWritten) {
                if (!queued) guard();
                else if (!expected.equals(string(field(hci, "recogText"))) || slot != integer(field(hci, "slotCurrent")))
                    result.put("capture_raced", true);
                result.put("png_start_valid", currentCaptureTarget());
                write("observation.json", map("engine_state", result.get("engine_state"), "slot", slot,
                    "sequence_index", sequenceIndex, "text", expected));
                observationWritten = true;
            }
            checkpointPng();
            if (observationWritten && Files.exists(root.resolve("capture.signal"))) {
                if (!queued) { guard(); break; }
                if (!expected.equals(string(field(hci, "recogText"))) || slot != integer(field(hci, "slotCurrent")))
                    result.put("capture_raced", true);
                if (sequenceIndex == sequence.size() - 1 && Files.exists(controlRoot.resolve("sequence-end.signal"))) {
                    require(!((BooleanValue)field(hci, "_isRecording")).value(), "recording_at_sequence_end");
                    result.put("terminal_ui_verified", true);
                    break;
                }
            }
        }
        finishTrial();
    }

    Map<String, Object> snapshot() {
        ObjectReference nativeSlot = (ObjectReference)field(hci, "cogSlotNative");
        Value text = field(hci, "recogText");
        return map("text", text == null ? null : string(text), "slot", slot,
            "actualWordID", null, "actualWordID_reason", "no_proven_task_bound_id_source",
            "word_count", nativeSlot == null ? null : integer(field(nativeSlot, "wordn")),
            "word_text", nativeSlot == null ? null : string(field(nativeSlot, "_wordText")));
    }

    void guard() {
        String actual = string(field(hci, "recogText"));
        boolean textMatches = expected.equals(actual);
        boolean slotMatches = slot == integer(field(hci, "slotCurrent"));
        if (!textMatches || !slotMatches) {
            result.put("target_verified", false);
            result.put("observed_target", actual.length() <= 4096 ? actual : "target_too_long");
        }
        require(textMatches, "target_mismatch");
        require(slotMatches, "slot_changed");
    }

    static Value argument(StackFrame frame, String name) throws Exception {
        Method method = frame.location().method();
        require(name.equals("buf") && method.name().equals("recognitionFrame")
            && method.signature().equals(INPUT) && method.declaringType().name().equals(TASK)
            && !method.isStatic(), "unsupported_argument_binding");
        require(method.location() != null && frame.location().codeIndex() == method.location().codeIndex(),
            "argument_not_at_method_entry");
        LocalVariable local;
        try {
            local = frame.visibleVariableByName(name);
        } catch (AbsentInformationException ex) {
            local = null;
        }
        Value value;
        if (local != null) {
            // ART can expose the correct named local while isArgument() is
            // false. Bind by the verified entry/signature and value shape.
            require(local.signature().equals("[S"), "argument_signature_mismatch_buf");
            value = frame.getValue(local);
        } else {
            // Some builds lack local names. Read argument zero only for the
            // exact two-argument signature above; never guess a JVM slot.
            List<Value> arguments;
            try {
                arguments = frame.getArgumentValues();
            } catch (InternalException ex) {
                throw new IllegalStateException("argument_values_unavailable_buf_jdwp_" + ex.errorCode(), ex);
            }
            require(arguments.size() == 2, "argument_count_mismatch");
            Value wrapper = arguments.get(1);
            require(wrapper instanceof ObjectReference && ((ObjectReference)wrapper).referenceType().signature()
                .equals("Lcom/selvy/sr/EduJPN/SelvySTT_pWrapper;"), "argument_wrapper_mismatch");
            value = arguments.get(0);
            System.out.println("ARGUMENT_SOURCE positional recognitionFrame[0]");
        }
        require(value instanceof ArrayReference, "argument_not_array_buf");
        ArrayReference buffer = (ArrayReference)value;
        require(buffer.referenceType().signature().equals("[S") && buffer.length() == 800,
            "argument_shape_mismatch_buf");
        return buffer;
    }

    @SuppressWarnings("unchecked")
    void handle(BreakpointEvent event) throws Exception {
        check();
        StackFrame frame = event.thread().frame(0);
        ObjectReference self = frame.thisObject();
        String name = event.location().method().name();
        if (name.equals("recognitionFrame")) {
            require(((ObjectReference)field(self, "this$0")).uniqueID() == hci.uniqueID(), "controller_changed");
            if (!sequence.isEmpty() && task != null && task.uniqueID() != self.uniqueID()
                    && finished && sequenceIndex + 1 < sequence.size()) {
                try { finishTrial(); }
                catch (Exception ex) {
                    task.enableCollection(); task = self; task.disableCollection(); finished = false;
                    cancelAt(event.thread());
                    throw ex;
                }
                task.enableCollection();
                activate(sequenceIndex + 1);
            }
            if (task != null && self.uniqueID() != task.uniqueID()) {
                task.enableCollection();
                task = self;
                task.disableCollection();
                finished = false;
                result.put("target_verified", false);
                cancelAt(event.thread());
                throw new IllegalStateException("unexpected_next_recording_canceled");
            }
            if (task == null) {
                task = self;
                task.disableCollection();
                if (deferredTarget) {
                    slot = integer(field(hci, "slotCurrent"));
                    require(requestedSlot < 0 || requestedSlot == slot, "slot_mismatch_at_first_frame");
                }
            }
            require(task.uniqueID() == self.uniqueID(), "recording_task_changed");
            try {
                require(Files.exists((controlRoot == null ? root : controlRoot).resolve("start.signal")), "recording_started_before_control");
                guard();
            } catch (RuntimeException ex) {
                cancelAt(event.thread());
                throw ex;
            }
            result.put("target_verified", true);
            if (frames == 0) {
                result.put("runtime", snapshot());
                write("recording.json", map("slot", slot, "text", expected));
            }
            ArrayReference buffer = (ArrayReference)argument(frame, "buf");
            require(buffer.referenceType().signature().equals("[S") && buffer.length() == 800
                && integer(field(self, "RECOG_FRAME_SAMPLES")) == 800, "unsupported_frame_shape");
            require(frames < 600, "frame_limit");
            int next = offset;
            List<Value> values = new ArrayList<Value>(buffer.length());
            for (int i = 0; i < buffer.length(); i++)
                values.add(vm.mirrorOf(next < samples.length ? samples[next++] : (short)0));
            buffer.setValues(values);
            require(buffer.getValues().equals(values), "full_frame_readback_mismatch");
            offset = next;
            frames++;
            readbacks++;
            suppliedFrameSamples += buffer.length();
            ((List<Object>)result.get("states")).add(integer(field(self, "recogState")));
            System.out.println("FRAME " + frames + " supplied=" + offset + " readback=ok");
        } else if (name.equals("onPostExecute")) {
            require(task != null && self.uniqueID() == task.uniqueID() && !finished, "unowned_result");
            guard();
            finished = true;
            resultAt = System.nanoTime();
            resultThread = event.thread().uniqueID();
            result.put("engine_state", integer(field(self, "recogState")));
            ((List<Object>)result.get("states")).add(result.get("engine_state"));
            result.put("full_input", offset == samples.length && frames == readbacks && frames > 0);
            try { System.out.println("ENGINE_LOG\n" + builder((ObjectReference)field(self, "debugInfo"))); }
            catch (RuntimeException ex) { System.out.println("ENGINE_LOG_UNAVAILABLE " + ex.getMessage()); }
            System.out.println("RESULT state=" + result.get("engine_state") + " supplied=" + offset + "/" + samples.length);
        } else if (name.equals("setAvgScore") && finished && event.thread().uniqueID() == resultThread) {
            // Attribute scores to this task's onPostExecute stack, not a prior slot.
            boolean owned = false;
            for (StackFrame parent : event.thread().frames())
                if (parent.location().method().name().equals("onPostExecute") && parent.thisObject() != null
                    && parent.thisObject().uniqueID() == task.uniqueID()) owned = true;
            if (!owned || integerState() != 1) return;
            guard();
            int count = integer(field(self, "wordn"));
            ObjectReference nativeSlot = (ObjectReference)field(hci, "cogSlotNative");
            require(nativeSlot != null && count == integer(field(nativeSlot, "wordn")), "score_count_mismatch");
            require(count > 0 && count <= 100, "invalid_current_score_count");
            ArrayReference positions = (ArrayReference)field(self, "_position");
            require(positions.length() == count * 2, "slot_position_count_mismatch");
            ArrayReference rows = (ArrayReference)field(hci, "m_ariScore");
            ArrayReference averages = (ArrayReference)field(hci, "m_ariAvgScore");
            require(rows.length() == 4 && averages.length() == 4, "score_shape_mismatch");
            Map<String, Object> scores = map("count", count, "count_source", "CognitionSlot.wordn",
                "word_text", string(field(self, "_wordText")), "slot", slot);
            String[] names = {"word", "duration", "pitch", "energy"};
            for (int i = 0; i < 4; i++) {
                require(integer(field(hci, "SCORE_" + names[i].toUpperCase(Locale.ROOT))) == i, "score_index_mismatch");
                ArrayReference row = (ArrayReference)rows.getValue(i);
                require(row.length() >= count, "score_array_too_short");
                List<Integer> values = new ArrayList<Integer>();
                for (Value value : row.getValues(0, count)) values.add(integer(value));
                scores.put(names[i], values);
                scores.put(names[i] + "_average", integer(averages.getValue(i)));
            }
            result.put("scores", scores);
            resultAt = System.nanoTime();
            System.out.println("SCORES " + json(scores));
        }
    }

    int integerState() { return ((Number)result.get("engine_state")).intValue(); }

    static String builder(ObjectReference object) {
        int count = integer(field(object, "count"));
        ArrayReference chars = (ArrayReference)field(object, "value");
        require(count >= 0 && count <= chars.length() && count <= 1000000, "invalid_builder");
        StringBuilder text = new StringBuilder(count);
        for (Value v : chars.getValues(0, count)) {
            require(v instanceof CharValue, "unsupported_builder_layout");
            text.append(((CharValue)v).value());
        }
        return text.toString();
    }

    void cancelOwnedTask() throws Exception {
        if (task == null || finished || canceled) return;
        long end = System.nanoTime() + 1200000000L;
        while (System.nanoTime() < end) {
            EventSet events = vm.eventQueue().remove(50);
            if (events == null) continue;
            try {
                for (Event event : events) {
                    if (!(event instanceof BreakpointEvent)) continue;
                    BreakpointEvent bp = (BreakpointEvent)event;
                    StackFrame frame = bp.thread().frame(0);
                    if (frame.thisObject() == null || frame.thisObject().uniqueID() != task.uniqueID()) continue;
                    if (bp.location().method().name().equals("onPostExecute")) { finished = true; return; }
                    if (!bp.location().method().name().equals("recognitionFrame")) continue;
                    cancelAt(bp.thread());
                    return;
                }
            } finally { events.resume(); }
        }
    }

    void cancelAt(ThreadReference thread) throws Exception {
        Method cancel = method(task.referenceType(), "cancel", "(Z)Z");
        for (EventRequest request : requests) request.disable();
        Value value = task.invokeMethod(thread, cancel, Arrays.<Value>asList(vm.mirrorOf(true)),
            ObjectReference.INVOKE_SINGLE_THREADED);
        canceled = value instanceof BooleanValue && ((BooleanValue)value).value();
    }

    void cleanup() {
        boolean detached = vm == null, removed = requests.isEmpty();
        boolean recordingReleased = task == null || finished;
        List<String> errors = new ArrayList<String>();
        if (vm != null) {
            try { cancelOwnedTask(); } catch (Exception ex) { errors.add("cancel: " + ex); }
            recordingReleased = task == null || finished;
            if (canceled && !recordingReleased) {
                try {
                    long end = System.nanoTime() + 500000000L;
                    do {
                        recordingReleased = !((BooleanValue)field(hci, "_isRecording")).value();
                        if (recordingReleased) break;
                        Thread.sleep(20);
                    } while (System.nanoTime() < end);
                } catch (Exception ex) { errors.add("recording_release: " + ex); }
            }
            try {
                vm.eventRequestManager().deleteEventRequests(requests);
                removed = true;
            } catch (Exception ex) { errors.add("requests: " + ex); }
            if (suspended) {
                try { vm.resume(); suspended = false; } catch (Exception ex) { errors.add("resume: " + ex); }
            }
            if (task != null) {
                try { task.enableCollection(); } catch (Exception ex) { errors.add("collection: " + ex); }
            }
            try { vm.dispose(); detached = true; System.out.println("DETACHED"); }
            catch (Exception ex) { errors.add("detach: " + ex); }
        }
        result.put("input", inputEvidence());
        result.put("cleanup", map("detached", detached, "requests_removed", removed,
            "recording_released", recordingReleased, "cancel_requested", canceled, "errors", errors));
    }

    Map<String, Object> inputEvidence() {
        return map("supplied_samples", offset, "total_samples", samples == null ? 0 : samples.length,
            "frames", frames, "readback_frames", readbacks, "padding_samples", suppliedFrameSamples - offset);
    }

    static String hash(byte[] data) throws Exception {
        StringBuilder out = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(data)) out.append(String.format("%02x", b & 255));
        return out.toString();
    }

    void write(String name, Object data) throws IOException {
        writeAt(root, name, data);
    }

    void writeAt(Path folder, String name, Object data) throws IOException {
        Path temp = folder.resolve(name + ".tmp");
        Files.write(temp, json(data).getBytes(StandardCharsets.UTF_8));
        Files.move(temp, folder.resolve(name), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }

    static String json(Object value) {
        if (value == null) return "null";
        if (value instanceof Number || value instanceof Boolean) return value.toString();
        if (value instanceof Map) {
            StringJoiner parts = new StringJoiner(",", "{", "}");
            for (Map.Entry<?, ?> entry : ((Map<?, ?>)value).entrySet()) parts.add(json(entry.getKey()) + ":" + json(entry.getValue()));
            return parts.toString();
        }
        if (value instanceof Iterable) {
            StringJoiner parts = new StringJoiner(",", "[", "]");
            for (Object entry : (Iterable<?>)value) parts.add(json(entry));
            return parts.toString();
        }
        StringBuilder out = new StringBuilder("\"");
        for (char c : value.toString().toCharArray()) {
            if (c == '\\' || c == '"') out.append('\\').append(c);
            else if (c < 32) out.append(String.format("\\u%04x", (int)c));
            else out.append(c);
        }
        return out.append('"').toString();
    }
}
