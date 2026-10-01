// Servo Trim Sync — плагін для Mission Planner.
//
// Коли SERVOn_MIN / SERVOn_TRIM / SERVOn_MAX змінює інша програма (Servo Trim Sync),
// Mission Planner отримує нове значення від політника, але відкриті сторінки
// (Servo Output, Full Parameter List) показують старі цифри до перевідкриття.
// Плагін одразу підставляє на них нові значення — без запису в політник і без змін
// у вашій роботі: поле, яке ви саме редагуєте, і рядки зі збереженими вами змінами не чіпаються.
//
// Встановлення: скопіювати цей файл у папку «plugins» поруч із MissionPlanner.exe
// і перезапустити Mission Planner (або кнопкою в Servo Trim Sync → налаштування).

using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using MissionPlanner;
using MissionPlanner.Controls;

namespace ServoTrimSyncRefresh
{
    public class Plugin : MissionPlanner.Plugin.Plugin
    {
        private static readonly Regex ServoParam = new Regex(@"^SERVO\d{1,2}_(MIN|MAX|TRIM)$");
        private readonly HashSet<string> pending = new HashSet<string>();
        private MAVLinkInterface port;
        private Timer timer;

        public override string Name { get { return "Servo Trim Sync — live SERVO MIN/TRIM/MAX"; } }
        public override string Version { get { return "1.0"; } }
        public override string Author { get { return "Servo Trim Sync"; } }

        public override bool Init()
        {
            return true;
        }

        public override bool Loaded()
        {
            timer = new Timer();
            timer.Interval = 250;
            timer.Tick += Tick;
            timer.Start();
            return true;
        }

        public override bool Exit()
        {
            if (timer != null)
                timer.Stop();
            if (port != null)
                port.OnPacketReceived -= OnPacket;
            return true;
        }

        // Потік читання MAVLink: лише запам'ятовуємо, що змінилося (MP уже оновив свій список параметрів).
        private void OnPacket(object sender, MAVLink.MAVLinkMessage message)
        {
            try
            {
                if (message.msgid != (uint)MAVLink.MAVLINK_MSG_ID.PARAM_VALUE)
                    return;
                var value = (MAVLink.mavlink_param_value_t)message.data;
                var name = Encoding.ASCII.GetString(value.param_id);
                var end = name.IndexOf('\0');
                if (end >= 0)
                    name = name.Substring(0, end);
                if (!ServoParam.IsMatch(name))
                    return;
                lock (pending)
                    pending.Add(name);
            }
            catch
            {
            }
        }

        // Потік інтерфейсу.
        private void Tick(object sender, EventArgs e)
        {
            try
            {
                var current = MainV2.comPort;
                if (!ReferenceEquals(current, port))
                {
                    if (port != null)
                        port.OnPacketReceived -= OnPacket;
                    port = current;
                    if (port != null)
                        port.OnPacketReceived += OnPacket;
                }

                List<string> names;
                lock (pending)
                {
                    if (pending.Count == 0)
                        return;
                    names = pending.ToList();
                    pending.Clear();
                }

                var parameters = MainV2.comPort.MAV.param;
                var wanted = new HashSet<string>(names.Where(n => parameters.ContainsKey(n)));
                if (wanted.Count == 0)
                    return;

                foreach (Form form in Application.OpenForms.Cast<Form>().ToList())
                {
                    foreach (var control in AllControls(form))
                    {
                        var number = control as MavlinkNumericUpDown;
                        if (number != null)
                        {
                            RefreshNumber(number, wanted, parameters);
                            continue;
                        }
                        if (control.GetType().Name == "ConfigRawParams")
                            RefreshRawParams(control, wanted, parameters);
                    }
                }
            }
            catch
            {
            }
        }

        private static IEnumerable<Control> AllControls(Control root)
        {
            foreach (Control child in root.Controls)
            {
                yield return child;
                foreach (var grandchild in AllControls(child))
                    yield return grandchild;
            }
        }

        private static void RefreshNumber(MavlinkNumericUpDown number, HashSet<string> wanted,
            MAVLink.MAVLinkParamList parameters)
        {
            if (number.ParamName == null || !wanted.Contains(number.ParamName) || number.ContainsFocus)
                return;
            var value = (decimal)(float)parameters[number.ParamName];
            if (number.Value == value)
                return;
            // setup() перечитує значення з MP без запису в політник (так само будує сторінку Servo Output).
            number.setup(800, 2200, 1, 1, number.ParamName, parameters);
        }

        private static void RefreshRawParams(Control page, HashSet<string> wanted, MAVLink.MAVLinkParamList parameters)
        {
            var flags = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
            var type = page.GetType();
            var gridField = type.GetField("Params", flags);
            var commandField = type.GetField("Command", flags);
            var valueField = type.GetField("Value", flags);
            var changesField = type.GetField("_changes", flags);
            var startupField = type.GetField("startup", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            if (gridField == null || commandField == null || valueField == null)
                return;
            var grid = gridField.GetValue(page) as DataGridView;
            var commandColumn = commandField.GetValue(page) as DataGridViewColumn;
            var valueColumn = valueField.GetValue(page) as DataGridViewColumn;
            var changes = changesField != null ? changesField.GetValue(page) as Hashtable : null;
            if (grid == null || commandColumn == null || valueColumn == null)
                return;

            var previous = startupField != null && (bool)startupField.GetValue(null);
            try
            {
                // Поки «startup», сторінка не вважає підстановку вашою правкою (не підсвічує, не пише).
                if (startupField != null)
                    startupField.SetValue(null, true);
                foreach (DataGridViewRow row in grid.Rows)
                {
                    var name = row.Cells[commandColumn.Index].Value as string;
                    if (name == null || !wanted.Contains(name))
                        continue;
                    if (changes != null && changes.ContainsKey(name))
                        continue; // ви вже змінили це значення вручну — не перезаписуємо
                    if (grid.IsCurrentCellInEditMode && grid.CurrentCell != null && grid.CurrentCell.RowIndex == row.Index)
                        continue;
                    var text = parameters[name].ToString();
                    if ((row.Cells[valueColumn.Index].Value as string) != text)
                        row.Cells[valueColumn.Index].Value = text;
                }
            }
            finally
            {
                if (startupField != null)
                    startupField.SetValue(null, previous);
            }
        }
    }
}
