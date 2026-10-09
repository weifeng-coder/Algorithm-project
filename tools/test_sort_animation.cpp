// Compile from the project root:
// g++ -O2 -std=c++17 -I common -I cpp/adaptsort -I app tools/test_sort_animation.cpp -o build/test_sort_animation.exe -lws2_32
#include <iostream>
#define main sort_application_main
#include "../app/server.cpp"
#undef main

static bool checkFrames(const vector<int>& input, AnimFn animate, const string& label,
                        int budget = 720) {
    Frames frames;
    frames.budget = budget;
    animate(input, frames);
    vector<int> expected = input;
    sort(expected.begin(), expected.end());
    if (frames.arrs.empty() || frames.arrs.size() != frames.meta.size()) {
        cerr << label << ": missing frames or metadata\n";
        return false;
    }
    for (size_t i = 0; i < frames.arrs.size(); ++i) {
        vector<int> values = frames.arrs[i];
        sort(values.begin(), values.end());
        if (values != expected) {
            cerr << label << ": frame " << i << " lost or duplicated input values\n";
            return false;
        }
        if (i && (frames.meta[i][0] < frames.meta[i-1][0] ||
                  frames.meta[i][1] < frames.meta[i-1][1])) {
            cerr << label << ": counters moved backwards\n";
            return false;
        }
    }
    if (frames.arrs.front() != input || frames.arrs.back() != expected ||
        (int)frames.arrs.size() > budget) {
        cerr << label << ": incorrect initial/final frame or frame budget\n";
        return false;
    }
    cout << "PASS " << label << " (" << frames.arrs.size() << " frames, "
         << frames.strategy << ")\n";
    return true;
}

int main() {
    bool ok = true;
    for (int n : {39, 40, 41, 64, 100, 513, 1000, 10000}) {
        vector<int> input(n);
        g_drng = 20260928u;
        for (int& value : input) value = (int)(drng01() * 100);
        ok = checkFrames(input, anim_counting, "counting n=" + to_string(n)) && ok;
        ok = checkFrames(input, anim_adapt, "adapt n=" + to_string(n),
                         max(200, min(720, 1600000 / n))) && ok;
    }
    for (const vector<int>& input : vector<vector<int>>{
             {9, 1, 7, 1, 3, 0, 9, 2},
             {9999, 256, 0, 255, 1024, 256, 1},
             {-10000, 256, -1, 0, 9999, -256, -1},
             {5, 5, 5, 5}, {0}, {}}) {
        ok = checkFrames(input, anim_radix, "radix signed/duplicate/empty") && ok;
        ok = checkFrames(input, anim_counting, "counting signed/duplicate/empty") && ok;
    }
    vector<int> wide(10000);
    g_drng = 20260928u;
    for (int& value : wide) value = (int)(drng01() * 10000);
    ok = checkFrames(wide, anim_radix, "radix n=10000 sampled", 200) && ok;
    return ok ? 0 : 1;
}
