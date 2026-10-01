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
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
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
        private System.Windows.Forms.Timer timer;

        // Самодіагностика: Servo Trim Sync читає цей файл і показує, чи плагін працює.
        private static readonly string StatusPath = Path.Combine(Path.GetTempPath(), "ServoTrimSyncRefresh.status");
        private DateTime loadedAt = DateTime.Now;
        private DateTime lastStatus = DateTime.MinValue;
        private long packets;
        private long servoValues;
        private long refreshed;
        private int controlsSeen = -1;
        private int scanned = -1;
        private string candidateTypes = "";
        private string pages = "";
        private DateTime lastScan = DateTime.MinValue;
        private string lastError = "";
        private static readonly Dictionary<Type, PropertyInfo> ParamNameProperty = new Dictionary<Type, PropertyInfo>();

        public override string Name { get { return "Servo Trim Sync — live SERVO MIN/TRIM/MAX"; } }
        public override string Version { get { return "1.0"; } }
        public override string Author { get { return "Servo Trim Sync"; } }

        public override bool Init()
        {
            return true;
        }

        public override bool Loaded()
        {
            timer = new System.Windows.Forms.Timer();
            timer.Interval = 250;
            timer.Tick += Tick;
            timer.Start();
            loadedAt = DateTime.Now;
            WriteStatus();
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
                Interlocked.Increment(ref packets);
                if (message.msgid != (uint)MAVLink.MAVLINK_MSG_ID.PARAM_VALUE)
                    return;
                var value = (MAVLink.mavlink_param_value_t)message.data;
                var name = Encoding.ASCII.GetString(value.param_id);
                var end = name.IndexOf('\0');
                if (end >= 0)
                    name = name.Substring(0, end);
                if (!ServoParam.IsMatch(name))
                    return;
                Interlocked.Increment(ref servoValues);
                lock (pending)
                    pending.Add(name);
            }
            catch (Exception ex)
            {
                lastError = "packet: " + ex.Message;
            }
        }

        // Потік інтерфейсу.
        private void Tick(object sender, EventArgs e)
        {
            if ((DateTime.Now - lastStatus).TotalSeconds >= 2)
                WriteStatus();
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
                    names = pending.ToList();
                    pending.Clear();
                }
                var periodic = (DateTime.Now - lastScan).TotalSeconds >= 2;
                if (names.Count == 0 && !periodic)
                    return;
                lastScan = DateTime.Now;

                var parameters = MainV2.comPort.MAV.param;
                var wanted = new HashSet<string>(names.Where(n => parameters.ContainsKey(n)));

                // Обходимо всі вікна MP і шукаємо поля, прив'язані до SERVOn_MIN/TRIM/MAX —
                // за властивістю ParamName або за назвою елемента (будь-якого типу, бо збірки MP різняться).
                var seen = 0;
                var total = 0;
                var types = new HashSet<string>();
                var pageTypes = new HashSet<string>();
                foreach (var root in Roots())
                {
                    foreach (var control in AllControls(root))
                    {
                        total++;
                        var typeName = control.GetType().Name;
                        if (typeName.IndexOf("Output", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            typeName.IndexOf("Servo", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            typeName.IndexOf("RawParam", StringComparison.OrdinalIgnoreCase) >= 0)
                            pageTypes.Add(typeName);
                        if (typeName == "ConfigRawParams")
                        {
                            if (wanted.Count > 0)
                                refreshed += RefreshRawParams(control, wanted, parameters);
                            continue;
                        }
                        var param = BoundParam(control);
                        if (param == null)
                            continue;
                        seen++;
                        types.Add(control.GetType().FullName);
                        if (wanted.Contains(param) && RefreshControl(control, param, parameters))
                            refreshed++;
                    }
                }
                controlsSeen = seen;
                scanned = total;
                candidateTypes = string.Join(",", types.Take(4));
                pages = string.Join(",", pageTypes.Take(6));
                WriteStatus();
            }
            catch (Exception ex)
            {
                lastError = "refresh: " + ex.Message;
            }
        }

        private static IEnumerable<Control> Roots()
        {
            var roots = new List<Control>();
            try
            {
                if (MainV2.instance != null)
                    roots.Add(MainV2.instance);
            }
            catch
            {
            }
            foreach (Form form in Application.OpenForms.Cast<Form>().ToList())
                if (!roots.Contains(form))
                    roots.Add(form);
            return roots;
        }

        private static string BoundParam(Control control)
        {
            var type = control.GetType();
            PropertyInfo property;
            if (!ParamNameProperty.TryGetValue(type, out property))
            {
                property = type.GetProperty("ParamName", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (property != null && property.PropertyType != typeof(string))
                    property = null;
                ParamNameProperty[type] = property;
            }
            string name = null;
            if (property != null)
            {
                try
                {
                    name = property.GetValue(control, null) as string;
                }
                catch
                {
                }
            }
            if (name == null || !ServoParam.IsMatch(name))
                name = control.Name;
            return name != null && ServoParam.IsMatch(name) ? name : null;
        }

        private static bool RefreshControl(Control control, string param, MAVLink.MAVLinkParamList parameters)
        {
            var value = (decimal)(float)parameters[param];
            // Не заважаємо, лише поки людина саме вводить число в це поле (текст ще не збігається зі значенням).
            var typing = control as UpDownBase;
            if (typing != null && control.ContainsFocus && control is NumericUpDown &&
                typing.Text != ((NumericUpDown)control).Value.ToString(System.Globalization.CultureInfo.CurrentCulture)
                && typing.Text != ((NumericUpDown)control).Value.ToString(System.Globalization.CultureInfo.InvariantCulture))
                return false;
            if (control is TextBoxBase && control.ContainsFocus)
                return false;
            var number = control as MavlinkNumericUpDown;
            if (number != null)
            {
                if (number.Value == value)
                    return false;
                // setup() перечитує значення з MP без запису в політник.
                number.setup(800, 2200, 1, 1, param, parameters);
                return true;
            }
            var numeric = control as NumericUpDown;
            if (numeric != null)
            {
                if (numeric.Value == value)
                    return false;
                if (value < numeric.Minimum)
                    numeric.Minimum = value;
                if (value > numeric.Maximum)
                    numeric.Maximum = value;
                numeric.Value = value; // те саме значення, що вже в політнику — повторний запис нешкідливий
                return true;
            }
            var text = ((float)parameters[param]).ToString(System.Globalization.CultureInfo.InvariantCulture);
            if ((control is TextBox || control is Label) && control.Text != text)
            {
                control.Text = text;
                return true;
            }
            return false;
        }

        private void WriteStatus()
        {
            lastStatus = DateTime.Now;
            try
            {
                File.WriteAllText(StatusPath, string.Format(
                    "loaded={0:o}\nnow={1:o}\nsubscribed={2}\npackets={3}\nservo_values={4}\nrefreshed={5}\n" +
                    "controls_seen={6}\nscanned={7}\ntypes={8}\npages={9}\nerror={10}\n",
                    loadedAt, DateTime.Now, port != null, Interlocked.Read(ref packets),
                    Interlocked.Read(ref servoValues), refreshed, controlsSeen, scanned, candidateTypes, pages,
                    lastError.Replace("\n", " ")));
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

        private static int RefreshRawParams(Control page, HashSet<string> wanted, MAVLink.MAVLinkParamList parameters)
        {
            var count = 0;
            var flags = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
            var type = page.GetType();
            var gridField = type.GetField("Params", flags);
            var commandField = type.GetField("Command", flags);
            var valueField = type.GetField("Value", flags);
            var changesField = type.GetField("_changes", flags);
            var startupField = type.GetField("startup", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            if (gridField == null || commandField == null || valueField == null)
                return 0;
            var grid = gridField.GetValue(page) as DataGridView;
            var commandColumn = commandField.GetValue(page) as DataGridViewColumn;
            var valueColumn = valueField.GetValue(page) as DataGridViewColumn;
            var changes = changesField != null ? changesField.GetValue(page) as Hashtable : null;
            if (grid == null || commandColumn == null || valueColumn == null)
                return 0;

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
                    {
                        row.Cells[valueColumn.Index].Value = text;
                        count++;
                    }
                }
            }
            finally
            {
                if (startupField != null)
                    startupField.SetValue(null, previous);
            }
            return count;
        }
    }
}
