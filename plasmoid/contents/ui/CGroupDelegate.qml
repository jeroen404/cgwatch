import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import "logic.js" as Logic

// One row of the popup cgroup list, plus its inline edit/unlimit expansion
// (mirrors tui.py's EditLimitsModal). Model roles come from
// Logic.computeRows() via the ListModel in main.qml, plus the
// `key`/`sevName` fields main.qml annotates each row with before appending
// (see main.qml's processPoll()/rebuildModel()). `model.key === model.name`
// (the raw cgroup dir name) -- that is also what gets passed as the unit to
// requestEditApply()/requestUnlimit(), matching the TUI's `instance_unit`;
// model.key is also passed as those functions' ctxKey argument (action
// attribution -- see main.qml's pendingActionKey).
Item {
    id: row

    required property var model
    property var rootItem

    readonly property color rowSevColor: row.rootItem.sevColor(model.sevName)
    readonly property string displayName: (Plasmoid.configuration.showDescriptions && model.description !== "")
                                           ? model.description : model.short_name

    // ------------------------------------------------------- editor state
    readonly property bool editorOpen: rootItem.editingKey === model.key
    // pendingActionKey is now always the real originating row's model.key
    // (never derived from editingKey/addPageOpen), so this correctly
    // covers both the inline-editor Save and a hover Unlimit fired from a
    // row that isn't the one currently being edited.
    readonly property bool busy: rootItem.actionInFlight
                                  && rootItem.pendingActionKey === model.key

    property string localError: ""
    property bool unlimitArmed: false

    onEditorOpenChanged: {
        if (editorOpen) {
            memField.text = model.edit_prefill_memory
            cpuField.text = model.edit_prefill_cpu
            // Failure-survives-popup-close: if this row has an unconsumed
            // failed result waiting (the popup was closed/reopened before
            // the Connections handler below ever got to show it), replay
            // it now instead of unconditionally clearing localError.
            var hint = Logic.consumeActionError(rootItem, model.key)
            localError = hint !== null ? hint : ""
            unlimitArmed = false
            disarmTimer.stop()
        }
    }

    Connections {
        target: row.rootItem
        function onActionResultSeqChanged() {
            var hint = Logic.consumeActionError(row.rootItem, model.key)
            if (hint !== null)
                row.localError = hint
        }
    }

    Timer {
        id: disarmTimer
        interval: 4000
        repeat: false
        onTriggered: row.unlimitArmed = false
    }

    function toggleUnlimitConfirm() {
        if (row.unlimitArmed) {
            disarmTimer.stop()
            row.unlimitArmed = false
            row.rootItem.requestUnlimit(model.name, model.key)
        } else {
            row.unlimitArmed = true
            disarmTimer.restart()
        }
    }

    width: ListView.view.width
    height: outerCol.implicitHeight + Kirigami.Units.smallSpacing * 2

    HoverHandler { id: hover }

    Rectangle { // hover highlight
        anchors.fill: parent
        radius: 4
        color: Kirigami.Theme.highlightColor
        opacity: (hover.hovered || row.editorOpen) ? 0.1 : 0
    }

    ColumnLayout {
        id: outerCol
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Kirigami.Units.smallSpacing
        spacing: Kirigami.Units.smallSpacing

        // ---- summary block (click anywhere here to open the editor) -----
        Item {
            id: summaryBlock
            Layout.fillWidth: true
            implicitHeight: summaryCol.implicitHeight

            MouseArea {
                anchors.fill: parent
                onClicked: row.rootItem.editingKey = model.key
            }

            ColumnLayout {
                id: summaryCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                spacing: 2

                RowLayout { // line 1: severity name + throttle + memory % + hover actions
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    Layout.minimumHeight: actionsRow.implicitHeight

                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        elide: Text.ElideMiddle
                        text: row.displayName
                        color: row.rowSevColor
                    }
                    PlasmaComponents.Label {
                        visible: model.throttled_delta > 0 && !hover.hovered
                        text: "⏱"
                        color: Kirigami.Theme.negativeTextColor
                    }
                    PlasmaComponents.Label {
                        visible: !hover.hovered
                        text: model.memory_percent.toFixed(0) + "%"
                        color: row.rowSevColor
                        font.bold: true
                    }

                    RowLayout { // hover-revealed quick actions
                        id: actionsRow
                        visible: hover.hovered
                        spacing: 0
                        PlasmaComponents.ToolButton {
                            icon.name: "document-edit"
                            PlasmaComponents.ToolTip.text: "Edit limits"
                            PlasmaComponents.ToolTip.visible: hovered
                            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                            onClicked: row.rootItem.editingKey = model.key
                        }
                        PlasmaComponents.ToolButton {
                            icon.name: "edit-delete-remove"
                            text: row.unlimitArmed ? "Really unlimit?" : ""
                            PlasmaComponents.ToolTip.text: "Unlimit"
                            PlasmaComponents.ToolTip.visible: hovered && !row.unlimitArmed
                            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                            onClicked: row.toggleUnlimitConfirm()
                        }
                    }
                }

                PlasmaComponents.Label { // line 2 (dim): used / limit · CPU: X% of Y%
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    text: Logic.humanBytes(model.memory_effective) + " / "
                          + (model.memory_max < 0 ? "max" : Logic.humanBytes(model.memory_max))
                          + " · CPU: " + (model.cpu_percent < 0 ? "—" : model.cpu_percent.toFixed(0) + "%")
                          + " of " + (model.cpu_quota_percent < 0 ? "max" : model.cpu_quota_percent.toFixed(0) + "%")
                    font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                    color: Kirigami.Theme.disabledTextColor

                    HoverHandler { id: memoryHover }
                    PlasmaComponents.ToolTip.text: Logic.humanBytes(model.memory_current) + " total ("
                                                   + Logic.humanBytes(model.memory_cache) + " cache)"
                    PlasmaComponents.ToolTip.visible: memoryHover.hovered
                    PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                }

                Rectangle { // thin horizontal memory progress bar
                    Layout.fillWidth: true
                    Layout.preferredHeight: 3
                    radius: 1.5
                    color: Kirigami.Theme.disabledTextColor
                    opacity: 0.3

                    Rectangle {
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        radius: 1.5
                        color: row.rowSevColor
                        width: parent.width * Math.min(100, Math.max(0, model.memory_percent)) / 100
                    }
                }
            }
        }

        // ---- inline editor --------------------------------------------------
        ColumnLayout {
            id: editorCol
            visible: row.editorOpen
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Separator { Layout.fillWidth: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                PlasmaComponents.Label {
                    text: "MemoryMax:"
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 5
                }
                PlasmaComponents.TextField {
                    id: memField
                    Layout.fillWidth: true
                    placeholderText: "e.g. 2G, 500M, max"
                    enabled: !row.busy
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                PlasmaComponents.Label {
                    text: "CPUQuota:"
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 5
                }
                PlasmaComponents.TextField {
                    id: cpuField
                    Layout.fillWidth: true
                    placeholderText: "e.g. 200%, max"
                    enabled: !row.busy
                }
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                visible: row.localError !== ""
                text: row.localError
                wrapMode: Text.WordWrap
                color: Kirigami.Theme.negativeTextColor
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            }

            ActionFooter {
                busy: row.busy
                onCancel: function () {
                    disarmTimer.stop()
                    row.unlimitArmed = false
                    row.rootItem.editingKey = ""
                }
                showMiddleButton: true
                middleText: row.unlimitArmed ? "Really unlimit?" : "Unlimit"
                onMiddle: function () { row.toggleUnlimitConfirm() }
                primaryText: "Save"
                // requestEditApply (isEdit=true): mirrors tui.py's
                // EditLimitsModal, which applies unconditionally
                // rather than rejecting an unknown/not-yet-running
                // unit (see main.qml).
                onPrimary: function () {
                    row.rootItem.requestEditApply(model.name, memField.text, cpuField.text, model.key)
                }
            }
        }
    }
}
