import { useState } from 'react';
import { FlatList, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { noteColor, noteIcon, useNotify } from '../hooks/useNotify';
import { ago } from '../lib/time';
import { Colors, fonts, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { Button, IconButton, IconChip } from './ui';

// bell for the top bar; opens the notification list
export default function Notifications({ size = 40 }: { size?: number }) {
  const { c } = useTheme();
  const s = useStyles(makeStyles);
  const { items, unread, markAllRead, remove, clear } = useNotify();
  const [open, setOpen] = useState(false);

  const close = () => {
    markAllRead();
    setOpen(false);
  };

  return (
    <>
      <IconButton
        icon={unread ? 'notifications' : 'notifications-outline'}
        size={size}
        onPress={() => setOpen(true)}
        label="Notifications"
      >
        {unread > 0 && (
          <View style={s.dot}>
            <Text style={s.dotText}>{unread > 9 ? '9+' : unread}</Text>
          </View>
        )}
      </IconButton>

      <Modal visible={open} transparent animationType="fade" onRequestClose={close}>
        <View style={s.backdrop}>
          <Pressable style={StyleSheet.absoluteFill} onPress={close} />
          <SafeAreaView edges={['top', 'bottom']} style={s.sheetWrap} pointerEvents="box-none">
            <View style={s.sheet}>
              <View style={s.head}>
                <Text style={s.title}>Notifications</Text>
                {items.length > 0 && <Button label="Clear all" variant="ghost" size="sm" onPress={clear} />}
                <IconButton icon="close" size={32} onPress={close} label="Close" />
              </View>

              <FlatList
                data={items}
                keyExtractor={(n) => n.id}
                style={{ flexGrow: 0 }}
                contentContainerStyle={items.length ? s.list : undefined}
                ListEmptyComponent={
                  <View style={s.empty}>
                    <Ionicons name="notifications-off-outline" size={28} color={c.faint} />
                    <Text style={s.emptyText}>All quiet. Skill results and robot alerts show up here.</Text>
                  </View>
                }
                renderItem={({ item, index }) => {
                  const color = noteColor(c, item.kind);
                  return (
                    <View style={[s.row, index === items.length - 1 && { borderBottomWidth: 0 }]}>
                      <IconChip
                        icon={noteIcon[item.kind]}
                        color={color}
                        bg={item.kind === 'info' ? undefined : c.panelHi}
                      />
                      <View style={{ flex: 1 }}>
                        <Text style={s.rowTitle}>{item.title}</Text>
                        {!!item.body && <Text style={s.rowBody}>{item.body}</Text>}
                        <Text style={s.rowMeta}>{ago(item.at)}</Text>
                      </View>
                      {!item.read && <View style={s.unread} />}
                      <IconButton
                        icon="close"
                        plain
                        size={28}
                        color={c.faint}
                        onPress={() => remove(item.id)}
                        label="Dismiss"
                      />
                    </View>
                  );
                }}
              />
            </View>
          </SafeAreaView>
        </View>
      </Modal>
    </>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    dot: {
      position: 'absolute',
      top: -3,
      right: -3,
      minWidth: 18,
      height: 18,
      borderRadius: 9,
      paddingHorizontal: 4,
      backgroundColor: c.accent,
      alignItems: 'center',
      justifyContent: 'center',
      borderWidth: 2,
      borderColor: c.bg,
    },
    dotText: { fontFamily: fonts.bold, fontSize: 9, lineHeight: 11, color: c.accentInk },
    backdrop: { flex: 1, backgroundColor: c.scrim },
    sheetWrap: { flex: 1, alignItems: 'flex-end', padding: 12 },
    sheet: {
      width: '100%',
      maxWidth: 400,
      maxHeight: '100%',
      backgroundColor: c.bg,
      borderRadius: radius.lg,
      padding: 20,
      boxShadow: shadowFloat,
    },
    head: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 },
    title: { flex: 1, ...type.h2, color: c.text },
    list: { backgroundColor: c.panel, borderRadius: radius.lg, paddingHorizontal: 16 },
    empty: { alignItems: 'center', gap: 10, paddingVertical: 30, paddingHorizontal: 20 },
    emptyText: { ...type.caption, fontSize: 12, color: c.faint, textAlign: 'center' },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: c.line,
    },
    rowTitle: { ...type.label, color: c.text },
    rowBody: { ...type.caption, color: c.dim, marginTop: 2 },
    rowMeta: { ...type.caption, color: c.faint, marginTop: 4 },
    unread: { width: 8, height: 8, borderRadius: 4, backgroundColor: c.accent },
  });
