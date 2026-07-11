import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

// All General fields wired to KConfigXT (see contents/config/main.xml).
KCM.SimpleKCM {
    id: page

    property string cfg_helperCommand
    property int cfg_pollInterval
    property int cfg_requestTimeout
    property int cfg_warningPercent
    property int cfg_criticalPercent

    Kirigami.FormLayout {
        QQC2.TextField {
            Kirigami.FormData.label: "Helper command:"
            Layout.fillWidth: true
            text: page.cfg_helperCommand
            onTextChanged: page.cfg_helperCommand = text
            placeholderText: "cgwatch-cli"
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "Leave this as the default unless you're developing cgwatch-cli itself "
                  + "or driving the mock helper (plasmoid/tools/mock-cgwatch-cli.sh) for testing."
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "Use an absolute path if plasmashell's PATH doesn't include it "
                  + "(plasmashell often doesn't see ~/.local/bin)."
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }

        Item { Kirigami.FormData.isSection: true }

        RowLayout {
            Kirigami.FormData.label: "Poll interval:"
            QQC2.SpinBox {
                from: 1
                to: 3600
                value: page.cfg_pollInterval
                onValueModified: page.cfg_pollInterval = value
            }
            QQC2.Label { text: "seconds" }
        }
        RowLayout {
            Kirigami.FormData.label: "Request timeout:"
            QQC2.SpinBox {
                from: 1
                to: 120
                value: page.cfg_requestTimeout
                onValueModified: page.cfg_requestTimeout = value
            }
            QQC2.Label { text: "seconds" }
        }

        Item { Kirigami.FormData.isSection: true }

        RowLayout {
            Kirigami.FormData.label: "Warning threshold:"
            QQC2.SpinBox {
                from: 1
                to: 100
                value: page.cfg_warningPercent
                onValueModified: page.cfg_warningPercent = value
            }
            QQC2.Label { text: "% memory" }
        }
        RowLayout {
            Kirigami.FormData.label: "Critical threshold:"
            QQC2.SpinBox {
                from: 1
                to: 100
                value: page.cfg_criticalPercent
                onValueModified: page.cfg_criticalPercent = value
            }
            QQC2.Label { text: "% memory" }
        }
        QQC2.Label {
            visible: page.cfg_warningPercent >= page.cfg_criticalPercent
            text: "Warning threshold should be lower than critical"
            color: Kirigami.Theme.negativeTextColor
            font: Kirigami.Theme.smallFont
        }
    }
}
