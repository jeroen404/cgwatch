import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import "logic.js" as Logic

PlasmoidItem {
    id: root

    // ---------------------------------------------------------------- state
    property var prevSamples: ({})          // Logic.computeRows()'s samples map
    property var lastRows: []               // last computed rows (for re-bucketize)
    property var buckets: ({
        total: 0, criticalCount: 0, warningCount: 0, calmCount: 0,
        throttledCount: 0, worstOverallPercent: 0, worstCriticalPercent: 0,
        worstWarningPercent: 0
    })
    property bool firstPollDone: false
    property double lastSuccessTs: 0
    property string errorKind: ""
    property string errorHint: ""
    property bool stale: false
    readonly property bool neverPolled: !firstPollDone

    // Throttle indicator state. CPU throttling flaps on/off every few
    // seconds; rather than pop an icon in and out (distracting layout
    // shifts), the compact view keeps a fixed throttle slot that just
    // recolors. throttleActive holds true for throttleHoldTimer's window
    // past the last throttle event so brief flaps read as a steady glow
    // instead of a blink.
    property bool throttleActive: false

    // Structural-freeze guard (TUI's modal guard): set while an inline
    // editor / the add-service page is open so rebuildModel() defers
    // structural reorders until the editor closes.
    property string editingKey: ""
    property bool addPageOpen: false

    // ---------------------------------------------------- action routing
    // candidates: raw `candidates[]` from the last successful dump
    // (unit/template/description/memory_current). Mirrored into
    // candidatesModel (below) via rebuildCandidates() -- AddServicePage's
    // ListView binds to candidatesListModel, never to this raw array, so a
    // volatile field (memory_current) changing every poll doesn't reset
    // its scroll position/selection.
    property var lastCandidates: []

    readonly property bool actionInFlight: api.actionInFlight
    // Sentinel pendingActionKey for the add-service page -- never a real
    // model.key (always a cgroup dir name, which always contains
    // ".service").
    readonly property string addPageContextKey: "__add__"
    // ctxKey snapshot of which row/page asked for the in-flight/just-
    // finished action, taken at request time and passed in explicitly by
    // the caller (CGroupDelegate passes model.key for both its hover and
    // inline-editor actions; AddServicePage passes addPageContextKey) --
    // NEVER derived from the global editingKey/addPageOpen. That
    // indirection was the bug: a hover Unlimit on a row that ISN'T the one
    // currently being edited would be mis-attributed to whatever other row
    // (or nothing) happened to be open in editingKey at the time.
    property string pendingActionKey: ""
    property var lastActionResult: null
    property int actionResultSeq: 0
    // Whether lastActionResult has already been displayed by a live
    // Connections handler. Set false when a new action starts, true once
    // some handler shows it -- lets the *next* open of the matching
    // editor/page replay an unconsumed failure even if the popup was
    // closed before the result arrived (see CGroupDelegate's
    // onEditorOpenChanged / AddServicePage.reset()).
    property bool lastActionConsumed: true

    // mem/cpu: raw user-typed strings ("" leaves that field untouched, see
    // logic.js buildApplyCommand). ctxKey: the originating row's model.key,
    // or addPageContextKey for the add-service page (see pendingActionKey
    // above). Returns false without side effects if an action is already
    // running (caller -- the open editor -- keeps Save/Unlimit disabled
    // while actionInFlight, so this is just a guard).
    function requestApply(unit, mem, cpu, ctxKey) {
        return requestApplyInternal(unit, mem, cpu, ctxKey, false)
    }

    // Same as requestApply, but applies with isEdit=true (cgwatch-cli
    // apply --edit): mirrors tui.py's EditLimitsModal, which applies
    // unconditionally instead of rejecting an unknown/not-yet-running
    // unit. Used by CGroupDelegate's inline editor Save button;
    // AddServicePage uses plain requestApply (isEdit=false).
    function requestEditApply(unit, mem, cpu, ctxKey) {
        return requestApplyInternal(unit, mem, cpu, ctxKey, true)
    }

    function requestApplyInternal(unit, mem, cpu, ctxKey, isEdit) {
        if (api.actionInFlight)
            return false
        pendingActionKey = ctxKey
        lastActionConsumed = false
        return api.applyLimits(unit, mem, cpu, isEdit)
    }

    function requestUnlimit(unit, ctxKey) {
        if (api.actionInFlight)
            return false
        pendingActionKey = ctxKey
        lastActionConsumed = false
        return api.unlimit(unit)
    }

    function handleActionFinished(result) {
        lastActionResult = result
        actionResultSeq += 1
        if (result.ok) {
            if (result.kind === "apply" && result.data && result.data.messages
                && result.data.messages.length)
                console.log("cgwatch: apply ok with warnings: " + result.data.messages.join("; "))
            // Only clear the editor/page that actually issued this action --
            // never touch the other one's state (Q2).
            if (editingKey !== "" && editingKey === pendingActionKey)
                editingKey = ""
            if (pendingActionKey === addPageContextKey)
                addPageOpen = false
            pendingActionKey = ""
            refreshNow()
        }
        // failure: editingKey/addPageOpen are left untouched -- the open
        // editor (matched via pendingActionKey) reads lastActionResult/
        // actionResultSeq and shows result.hint inline, marking it consumed.
    }

    ListModel { id: cgroupModel }
    readonly property var cgroupListModel: cgroupModel

    ListModel { id: candidatesModel }
    readonly property var candidatesListModel: candidatesModel

    // ------------------------------------------------------------ helpers
    function sevColor(sev) {
        switch (sev) {
        case "critical": return Kirigami.Theme.negativeTextColor
        case "warning":  return Kirigami.Theme.neutralTextColor
        case "calm":     return Kirigami.Theme.positiveTextColor
        }
        return Kirigami.Theme.textColor
    }

    function cfgLogic() {
        return {
            helperCommand: Plasmoid.configuration.helperCommand,
            warningPercent: Plasmoid.configuration.warningPercent,
            criticalPercent: Plasmoid.configuration.criticalPercent,
            requestTimeout: Plasmoid.configuration.requestTimeout
        }
    }

    // --------------------------------------------------------- poll cycle
    CGWatchApi {
        id: api
        helperCommand: Plasmoid.configuration.helperCommand
        requestTimeout: Plasmoid.configuration.requestTimeout
        onPollFinished: function (result) { root.processPoll(result) }
        onActionFinished: function (result) { root.handleActionFinished(result) }
    }

    function startPoll() { api.poll() }

    function refreshNow() {
        api.abort()
        api.poll()
    }

    function processPoll(result) {
        if (!result.ok) {
            pollFailed(result)
            return
        }
        var cfg = cfgLogic()
        var computed = Logic.computeRows(result.data, prevSamples, cfg)
        prevSamples = computed.samples
        var rows = computed.rows
        for (var i = 0; i < rows.length; i++) {
            rows[i].key = rows[i].name
            rows[i].sevName = Logic.memSevName(rows[i].memory_percent, cfg)
        }
        lastRows = rows
        buckets = Logic.bucketize(rows, cfg.warningPercent, cfg.criticalPercent)
        if (buckets.throttledCount > 0) {
            throttleActive = true
            throttleHoldTimer.restart()
        }
        rebuildCandidates((result.data && result.data.candidates) || [])
        firstPollDone = true
        lastSuccessTs = Date.now()
        errorKind = ""
        errorHint = ""
        rebuildModel(rows)
        updateStale()
    }

    function pollFailed(result) {
        errorKind = result.kind
        errorHint = result.hint || ""
        console.log("cgwatch: poll failed (" + result.kind + "): " + errorHint)
        // failed polls keep the last-known rows/buckets/model untouched --
        // only the error/stale state changes (dim + ⚠ in the UI)
        updateStale()
    }

    // ------------------------------------------------ derived state & model
    function reBucketize() {
        var cfg = cfgLogic()
        for (var i = 0; i < lastRows.length; i++)
            lastRows[i].sevName = Logic.memSevName(lastRows[i].memory_percent, cfg)
        buckets = Logic.bucketize(lastRows, cfg.warningPercent, cfg.criticalPercent)
        rebuildModel(lastRows)
    }

    property string lastModelSignature: ""

    function rebuildModel(rows) {
        var sig = JSON.stringify(rows)
        if (sig === lastModelSignature)
            return
        var sameOrder = rows.length === cgroupModel.count
        if (sameOrder) {
            for (var i = 0; i < rows.length; i++) {
                if (cgroupModel.get(i).key !== rows[i].key) {
                    sameOrder = false
                    break
                }
            }
        }
        if (!sameOrder && (root.editingKey !== "" || root.addPageOpen)) {
            // TUI's modal guard: a structural reorder while an editor/add
            // page is open would shift row indices out from under it --
            // defer the rebuild. lastModelSignature is only updated on an
            // applied change, so the next poll (or the editor closing)
            // retries it.
            return
        }
        lastModelSignature = sig
        if (sameOrder) {
            // Same key order: pure in-place role updates keep the delegates
            // (and any open editor state) alive.
            for (var j = 0; j < rows.length; j++)
                cgroupModel.set(j, rows[j])
            return
        }
        // Structural change (rows added/removed/reordered): full rebuild.
        // Do NOT be tempted to sync with ListModel.move()+set() -- that left
        // overlapping stale delegates in the visible ListView on Plasma 6.3
        // (knagger's finding).
        cgroupModel.clear()
        for (var k = 0; k < rows.length; k++)
            cgroupModel.append(rows[k])
    }

    property string lastCandidatesSignature: ""

    // Mirrors rebuildModel() above, keyed by the candidate's stable
    // identity field `unit`. Deliberately excludes memory_current (and any
    // other volatile field) from the structural signature -- that field
    // changing every poll is exactly what must NOT trigger a rebuild that
    // would reset AddServicePage's ListView scroll position/selection.
    //
    // force=true bypasses both the no-op skip and the addPageOpen freeze
    // guard below; used only by onAddPageOpenChanged to take one fresh
    // snapshot right as the page opens, before it starts freezing.
    function rebuildCandidates(list, force) {
        lastCandidates = list
        var sig = JSON.stringify(list.map(function (c) { return c.unit }))
        if (!force && sig === lastCandidatesSignature)
            return
        var sameOrder = list.length === candidatesModel.count
        if (sameOrder) {
            for (var i = 0; i < list.length; i++) {
                if (candidatesModel.get(i).unit !== list[i].unit) {
                    sameOrder = false
                    break
                }
            }
        }
        if (!force && !sameOrder && root.addPageOpen) {
            // TUI's modal guard, mirrored: a structural reorder (a service
            // started/stopped being a candidate, or the memory-desc sort
            // reordered the keys) while the add-service page is open would
            // shift row indices out from under the user's click -- defer
            // it. lastCandidatesSignature is only updated on an applied
            // change, so the next poll after the page closes retries it.
            return
        }
        lastCandidatesSignature = sig
        if (sameOrder) {
            // Same unit order: pure in-place role updates (memory_current/
            // description) keep the ListView's scroll position/selection.
            for (var j = 0; j < list.length; j++)
                candidatesModel.set(j, list[j])
            return
        }
        // Structural change: full rebuild. Do NOT be tempted to sync with
        // ListModel.move()+set() -- see rebuildModel()'s comment above
        // (knagger's finding: overlapping stale delegates on Plasma 6.3).
        candidatesModel.clear()
        for (var k = 0; k < list.length; k++)
            candidatesModel.append(list[k])
    }

    // One fresh full rebuild right as the add page opens, so it shows
    // current candidates before rebuildCandidates() starts freezing
    // structural changes for the rest of the time it stays open.
    onAddPageOpenChanged: {
        if (addPageOpen)
            rebuildCandidates(lastCandidates, true)
    }

    function updateStale() {
        stale = lastSuccessTs > 0
            && (Date.now() - lastSuccessTs > 3 * Math.max(1, Plasmoid.configuration.pollInterval) * 1000)
    }

    // -------------------------------------------------------------- timers
    Timer {
        id: pollTimer
        interval: Math.max(1, Plasmoid.configuration.pollInterval) * 1000
        running: true
        repeat: true
        onTriggered: root.startPoll()   // skips silently while a poll is in flight
    }

    Timer {
        id: staleTimer
        interval: 5000
        running: true
        repeat: true
        onTriggered: root.updateStale()
    }

    // Anti-strobe hold: restarted on every poll that sees throttling, so a
    // throttle that flaps on/off keeps the indicator amber steadily until
    // ~5s after the last throttle event, then it fades back to idle.
    Timer {
        id: throttleHoldTimer
        interval: 5000
        onTriggered: root.throttleActive = false
    }

    // ------------------------------------------------------ config changes
    Connections {
        target: Plasmoid.configuration
        function onHelperCommandChanged() {
            root.prevSamples = {}
            // An in-flight action was launched against the old
            // helperCommand -- its eventual reply would be misleading (or
            // arrive after a differently-configured helper is expected).
            api.abortAction()
            root.refreshNow()
        }
        function onPollIntervalChanged() {
            pollTimer.restart()
            root.updateStale()
        }
        // thresholds re-bucket/re-color from the last-known rows, no re-poll
        function onWarningPercentChanged() { root.reBucketize() }
        function onCriticalPercentChanged() { root.reBucketize() }
    }

    Component.onCompleted: {
        // version in the journal makes cross-machine install skew diagnosable
        console.log("cgwatch: starting v" + (Plasmoid.metaData.version || "?"))
        startPoll()
    }

    // ------------------------------------------------------ representations
    preferredRepresentation: (Plasmoid.formFactor === PlasmaCore.Types.Horizontal
                              || Plasmoid.formFactor === PlasmaCore.Types.Vertical)
                             ? compactRepresentation : null
    switchWidth: Kirigami.Units.gridUnit * 18
    switchHeight: Kirigami.Units.gridUnit * 12

    toolTipMainText: {
        if (neverPolled) return "CGWatch: no data yet"
        if (buckets.total === 0) return "No limited cgroups"
        var parts = []
        if (buckets.criticalCount) parts.push(buckets.criticalCount + " critical")
        if (buckets.warningCount) parts.push(buckets.warningCount + " warning")
        var s = parts.length ? parts.join(", ") : "all calm"
        s += " · " + buckets.total + " limited"
        if (buckets.throttledCount) s += " · " + buckets.throttledCount + " throttled"
        return s
    }
    toolTipSubText: {
        var s = ""
        if (errorKind !== "")
            s += "Poll failed: " + errorHint + "\n"
        if (lastSuccessTs > 0)
            s += "Updated " + Qt.formatTime(new Date(lastSuccessTs), "hh:mm:ss") + (stale ? " (stale)" : "")
        else
            s += "Waiting for first successful poll"
        return s
    }

    // Visual identity deliberately differs from knagger's round dots: a
    // gauge/bar language (see plan §3).
    compactRepresentation: Item {
        id: compact
        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? 0 : grid.implicitWidth + Kirigami.Units.smallSpacing * 2
        Layout.preferredWidth: vertical ? -1 : grid.implicitWidth + Kirigami.Units.smallSpacing * 2
        Layout.minimumHeight: vertical ? grid.implicitHeight + Kirigami.Units.smallSpacing * 2
                                       : Kirigami.Units.iconSizes.small
        Layout.preferredHeight: vertical ? grid.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1

        Accessible.name: root.toolTipMainText

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.MiddleButton
            onClicked: function (mouse) {
                if (mouse.button === Qt.MiddleButton)
                    root.refreshNow()
                else
                    root.expanded = !root.expanded
            }
        }

        GridLayout {
            id: grid
            anchors.centerIn: parent
            flow: compact.vertical ? GridLayout.TopToBottom : GridLayout.LeftToRight
            columns: compact.vertical ? 1 : -1
            rows: compact.vertical ? -1 : 1
            columnSpacing: Kirigami.Units.smallSpacing
            rowSpacing: Kirigami.Units.smallSpacing / 2
            // stale/failed: whole row dimmed (never-polled has its own,
            // separately-dimmed "?" chip below, so it's excluded here)
            opacity: (!root.neverPolled && (root.errorKind !== "" || root.stale)) ? 0.5 : 1

            // Two fixed slots, always present and same size, so the compact
            // view never resizes or pops elements in/out (see plan §C).
            // Critical/warning counts live in the tooltip + popup, not here.

            // --- Slot 1: memory gauge (always present, fixed size) ---
            // A single fixed-size Rectangle track (a well-behaved leaf item in
            // the layout, like knagger's dots) whose semi-transparent COLOR --
            // not an opacity property -- keeps it dim while the child fill
            // renders at full opacity. The fill's width derives from the fixed
            // 16px constant, NEVER from parent.width: reading the layout-set
            // width here fed grid.implicitWidth -> compact.preferredWidth ->
            // the panel -> back to this item's width, a QtQuick.Layouts
            // feedback loop that silently pegged the main thread at 100% CPU
            // (Qt doesn't flag it as a binding loop).
            Rectangle {
                id: memGauge
                implicitWidth: 16
                implicitHeight: 6
                radius: 3
                color: Qt.rgba(Kirigami.Theme.disabledTextColor.r,
                               Kirigami.Theme.disabledTextColor.g,
                               Kirigami.Theme.disabledTextColor.b, 0.3)

                Rectangle { // severity-colored fill
                    visible: !root.neverPolled && root.buckets.total > 0
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    radius: 3
                    color: root.buckets.criticalCount > 0 ? root.sevColor("critical")
                         : (root.buckets.warningCount > 0 ? root.sevColor("warning")
                                                          : root.sevColor("calm"))
                    width: Math.max(2, memGauge.implicitWidth
                                        * Math.min(100, Math.max(0, root.buckets.worstOverallPercent)) / 100)
                }
            }

            // --- Slot 2: CPU-throttle indicator (always present when enabled) ---
            // Never pops in/out with throttle state -- it stays put and just
            // recolors: dim grey when idle, muted amber while root.throttleActive
            // (held ~5s past the last throttle event). Never red -- red is
            // reserved for memory-critical. The config toggle hides the whole
            // slot for users who don't care; that's a deliberate, non-flapping
            // choice, not per-poll flicker.
            PlasmaComponents.Label {
                visible: Plasmoid.configuration.showThrottleBadge
                text: "⏱"
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                opacity: root.neverPolled ? 0.5 : 1
                color: root.throttleActive ? Kirigami.Theme.neutralTextColor
                                           : Kirigami.Theme.disabledTextColor
            }

            // poll-failure badge: appended, never replaces last-known state
            PlasmaComponents.Label {
                visible: root.errorKind !== "" && !root.neverPolled
                text: "⚠"
                color: Kirigami.Theme.negativeTextColor
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            }
        }
    }

    fullRepresentation: FullRepresentation {
        rootItem: root
    }
}
