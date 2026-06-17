# Translation workflow
# 1. Extract strings from source
update-ts:
	pyside6-lupdate QuickSync4LinuxGui/QuickSync4LinuxGui/gui.py -ts QuickSync4LinuxGui/QuickSync4LinuxGui/lang/de.ts
# 2. Compile .ts to .qm
compile-ts:
	lrelease6 QuickSync4LinuxGui/QuickSync4LinuxGui/lang/de.ts
# Do both
i18n: update-ts compile-ts