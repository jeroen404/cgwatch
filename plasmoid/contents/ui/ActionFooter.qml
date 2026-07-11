import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

// Shared "BusyIndicator + spacer + Cancel + primary action" footer row used
// by CGroupDelegate's inline editor and AddServicePage. Layout/spacing and
// the disabled-while-busy behavior are identical in both call sites; only
// the primary button's label/enabled-condition/click handler (and Cancel's
// click handler) differ, so those are passed in by the caller.
RowLayout {
    id: footer

    // true while the action this footer belongs to is in flight -- disables
    // both buttons and drives the BusyIndicator.
    property bool busy: false

    // Cancel button wiring.
    property var onCancel: function () {}

    // Primary action button wiring.
    property string primaryText: ""
    property bool primaryEnabled: true
    property var onPrimary: function () {}

    // Optional extra button between Cancel and the primary action --
    // CGroupDelegate's inline editor has an "Unlimit"/"Really unlimit?"
    // button there; AddServicePage has none, so this stays hidden (and,
    // per QtQuick.Layouts, takes up no space) unless a caller opts in.
    property bool showMiddleButton: false
    property string middleText: ""
    property var onMiddle: function () {}

    Layout.fillWidth: true
    spacing: Kirigami.Units.smallSpacing

    PlasmaComponents.BusyIndicator {
        visible: footer.busy
        running: footer.busy
        Layout.preferredWidth: Kirigami.Units.iconSizes.small
        Layout.preferredHeight: Kirigami.Units.iconSizes.small
    }
    Item { Layout.fillWidth: true }
    PlasmaComponents.Button {
        text: "Cancel"
        enabled: !footer.busy
        onClicked: footer.onCancel()
    }
    PlasmaComponents.Button {
        visible: footer.showMiddleButton
        text: footer.middleText
        enabled: !footer.busy
        onClicked: footer.onMiddle()
    }
    PlasmaComponents.Button {
        text: footer.primaryText
        enabled: !footer.busy && footer.primaryEnabled
        onClicked: footer.onPrimary()
    }
}
