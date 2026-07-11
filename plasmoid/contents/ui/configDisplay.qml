import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

// Both Display fields wired to KConfigXT (see contents/config/main.xml).
KCM.SimpleKCM {
    id: page

    property bool cfg_showDescriptions
    property bool cfg_showThrottleBadge

    Kirigami.FormLayout {
        QQC2.CheckBox {
            Kirigami.FormData.label: "Popup:"
            text: "Show service descriptions instead of unit names"
            checked: page.cfg_showDescriptions
            onToggled: page.cfg_showDescriptions = checked
        }
        QQC2.CheckBox {
            Kirigami.FormData.label: "Panel:"
            text: "Show the CPU-throttle indicator"
            checked: page.cfg_showThrottleBadge
            onToggled: page.cfg_showThrottleBadge = checked
        }
    }
}
