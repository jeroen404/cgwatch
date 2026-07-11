import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

// Popup: branded header, error banner, cgroup list (or the add-service page
// in its place), footer. Inline edit/unlimit lives in CGroupDelegate; the
// add-service flow in AddServicePage -- both driven by editingKey/
// addPageOpen on rootItem (main.qml).
Item {
    id: fullRoot

    // the PlasmoidItem in main.qml (state, model, actions)
    property var rootItem

    Layout.minimumWidth: Kirigami.Units.gridUnit * 18
    Layout.minimumHeight: Kirigami.Units.gridUnit * 12
    Layout.preferredWidth: Kirigami.Units.gridUnit * 22
    Layout.preferredHeight: Kirigami.Units.gridUnit * 24

    function summaryHtml() {
        var b = fullRoot.rootItem.buckets
        if (fullRoot.rootItem.neverPolled) return "No data yet"
        var parts = []
        if (b.criticalCount > 0)
            parts.push("<font color=\"" + fullRoot.rootItem.sevColor("critical") + "\">"
                       + b.criticalCount + " critical</font>")
        if (b.warningCount > 0)
            parts.push("<font color=\"" + fullRoot.rootItem.sevColor("warning") + "\">"
                       + b.warningCount + " warning</font>")
        if (parts.length === 0)
            parts.push("<font color=\"" + fullRoot.rootItem.sevColor("calm") + "\">all calm</font>")
        parts.push(b.total + " limited")
        return parts.join(" · ")
    }

    // focus the cgroup list when the popup opens (knagger's FullRepresentation.qml)
    Connections {
        target: fullRoot.rootItem
        function onExpandedChanged() {
            if (fullRoot.rootItem.expanded)
                cgroupList.forceActiveFocus()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.smallSpacing
        spacing: Kirigami.Units.smallSpacing

        // ---- branded header: icon + title + summary + actions -------------
        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Icon {
                source: "cgwatch"
                Layout.preferredWidth: Kirigami.Units.iconSizes.small
                Layout.preferredHeight: Kirigami.Units.iconSizes.small
            }
            PlasmaComponents.Label {
                text: "CGWatch"
                font.bold: true
            }
            PlasmaComponents.Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                textFormat: Text.StyledText
                text: fullRoot.summaryHtml()
            }
            PlasmaComponents.ToolButton {
                icon.name: "view-refresh"
                PlasmaComponents.ToolTip.text: "Refresh now"
                PlasmaComponents.ToolTip.visible: hovered
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                onClicked: fullRoot.rootItem.refreshNow()
            }
            PlasmaComponents.ToolButton {
                text: "Add service…"
                icon.name: "list-add"
                enabled: !fullRoot.rootItem.addPageOpen
                PlasmaComponents.ToolTip.text: "Add a limit to a running service"
                PlasmaComponents.ToolTip.visible: hovered
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                onClicked: fullRoot.rootItem.addPageOpen = true
            }
        }

        Rectangle { // thin accent underline -- distinct from knagger's plain summary line
            Layout.fillWidth: true
            Layout.preferredHeight: 2
            color: Kirigami.Theme.highlightColor
        }

        // ---- table-style column header --------------------------------------
        RowLayout {
            Layout.fillWidth: true
            visible: !fullRoot.rootItem.neverPolled && !fullRoot.rootItem.addPageOpen
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: "Name"
                font.bold: true
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                color: Kirigami.Theme.disabledTextColor
            }
            PlasmaComponents.Label {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 3
                horizontalAlignment: Text.AlignRight
                text: "Memory"
                font.bold: true
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                color: Kirigami.Theme.disabledTextColor
            }
            PlasmaComponents.Label {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 3
                horizontalAlignment: Text.AlignRight
                text: "CPU"
                font.bold: true
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                color: Kirigami.Theme.disabledTextColor
            }
        }

        // ---- error banner (still showing last-known data) -------------------
        Rectangle {
            visible: fullRoot.rootItem.errorKind !== "" && !fullRoot.rootItem.neverPolled
            Layout.fillWidth: true
            radius: 4
            color: Kirigami.Theme.negativeBackgroundColor
            implicitHeight: errLabel.implicitHeight + Kirigami.Units.smallSpacing * 2
            PlasmaComponents.Label {
                id: errLabel
                anchors.fill: parent
                anchors.margins: Kirigami.Units.smallSpacing
                text: "⚠ " + fullRoot.rootItem.errorHint + " — showing data from "
                      + Qt.formatTime(new Date(fullRoot.rootItem.lastSuccessTs), "hh:mm")
                wrapMode: Text.WordWrap
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                color: Kirigami.Theme.negativeTextColor
            }
        }

        // ---- cgroup list / add-service page ------------------------------------
        // Simple visibility swap (both siblings in this ColumnLayout; an
        // invisible item claims no layout space) -- editor state for either
        // lives in main.qml (editingKey/addPageOpen) so it survives popup
        // close/reopen (Plasma destroys/recreates FullRepresentation, not root).
        PlasmaComponents.ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !fullRoot.rootItem.addPageOpen

            ListView {
                id: cgroupList
                clip: true
                model: fullRoot.rootItem.cgroupListModel
                spacing: 1
                currentIndex: 0
                keyNavigationEnabled: true

                delegate: CGroupDelegate {
                    rootItem: fullRoot.rootItem
                }

                // empty state -- polling fine, nothing limited
                Kirigami.PlaceholderMessage {
                    anchors.centerIn: parent
                    width: parent.width - Kirigami.Units.gridUnit * 2
                    visible: cgroupList.count === 0 && !fullRoot.rootItem.neverPolled
                             && fullRoot.rootItem.errorKind === ""
                    icon.name: "checkmark"
                    text: "No limited cgroups"
                    explanation: fullRoot.rootItem.lastSuccessTs > 0
                        ? "Updated " + Qt.formatTime(new Date(fullRoot.rootItem.lastSuccessTs), "hh:mm:ss")
                        : ""
                }

                // error state -- never polled successfully yet (includes the
                // missing-helper hint straight from Logic.errorHint("missing"))
                Kirigami.PlaceholderMessage {
                    anchors.centerIn: parent
                    width: parent.width - Kirigami.Units.gridUnit * 2
                    visible: fullRoot.rootItem.neverPolled
                    icon.name: "network-disconnect"
                    text: fullRoot.rootItem.errorKind !== "" ? "Cannot reach cgwatch-cli" : "Waiting for first poll…"
                    explanation: fullRoot.rootItem.errorHint
                    helpfulAction: Kirigami.Action {
                        text: "Configure…"
                        icon.name: "configure"
                        onTriggered: Plasmoid.internalAction("configure").trigger()
                    }
                }
            }
        }

        AddServicePage {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: fullRoot.rootItem.addPageOpen
            rootItem: fullRoot.rootItem
        }

        // ---- footer: total + last update --------------------------------------
        RowLayout {
            Layout.fillWidth: true
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: fullRoot.rootItem.buckets.total + " limited cgroup"
                      + (fullRoot.rootItem.buckets.total === 1 ? "" : "s")
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                color: Kirigami.Theme.disabledTextColor
            }
            PlasmaComponents.Label {
                visible: fullRoot.rootItem.lastSuccessTs > 0
                text: "Updated " + Qt.formatTime(new Date(fullRoot.rootItem.lastSuccessTs), "hh:mm:ss")
                      + (fullRoot.rootItem.stale ? " (stale)" : "")
                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                color: Kirigami.Theme.disabledTextColor
            }
        }
    }
}
