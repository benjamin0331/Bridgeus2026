extends SceneTree
# AnonNames.pick() 的自我檢查。專案沒有測試框架（見 CLAUDE.md「Running」），
# 但發號規則是這個功能唯一有分支的邏輯、又會在改代號池時被動到，所以比照
# scripts/recolor_frog.py 的 --demo，留一支跑得起來的檢查：
#
#   "<Godot 執行檔>" --headless --path <本專案> --script Globals/anon_names_check.gd
#
# 全過印「OK」、離開碼 0；有任何一項不符就逐項印出並回非 0。

func _init():
	var fails := 0
	fails += _eq(AnonNames.pick([]), "飽食海星", "空房時拿池子第一個")
	fails += _eq(AnonNames.pick(["飽食海星"]), "噴水龍", "跳過已佔用的")
	fails += _eq(AnonNames.pick(["噴水龍"]), "飽食海星", "釋放的名字可以被撿回")
	# 10 個全佔滿 → 第 11 位起加編號
	var all_ten := AnonNames.POOL.duplicate()
	fails += _eq(AnonNames.pick(all_ten), "飽食海星 2", "池子用完加編號")
	var eleven := all_ten.duplicate()
	eleven.append("飽食海星 2")
	fails += _eq(AnonNames.pick(eleven), "噴水龍 2", "第二圈繼續往下")
	# 連續發 25 個名字全部相異
	var handed := []
	for i in range(25):
		handed.append(AnonNames.pick(handed))
	var uniq := {}
	for n in handed:
		uniq[n] = true
	fails += _eq(uniq.size(), 25, "連發 25 個名字不重複")
	fails += _eq(handed[24], "香香泥 3", "第 25 個落在第三圈")

	if fails == 0:
		print("OK：全部通過")
	else:
		print("FAIL：%d 項未通過" % fails)
	quit(1 if fails > 0 else 0)

func _eq(got, want, what: String) -> int:
	if got == want:
		return 0
	print("  ✗ %s：得到 %s，預期 %s" % [what, got, want])
	return 1
