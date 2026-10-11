#pragma once
#include <string>
#include <vector>
#include <cstdint>
#include <utility>

namespace jsjson {

struct Value {
    enum Type { NUL, BOOL, NUMBER, STRING, OBJECT, ARRAY };
    Type type = NUL;
    bool b = false;
    std::string raw, s;
    std::vector<std::pair<std::string, Value>> obj;
    std::vector<Value> arr;
    size_t begin = 0, end = 0;
};

struct Error {
    std::string msg;
    size_t pos = 0;
    int line = 1, col = 1, status = 400;
};

class Parser {
    const std::string& x;
    size_t p = 0, nodes = 0;
    int depth = 0;
    Error* error;
    static constexpr size_t MAX_STRING = 64u * 1024u;
    static constexpr size_t MAX_NODES = 2000000;

    bool bad(const char* message, int status = 400) {
        if (error && error->msg.empty()) {
            error->msg = message;
            error->pos = p;
            error->status = status;
            error->line = error->col = 1;
            for (size_t i = 0; i < p && i < x.size(); ++i) {
                if (x[i] == '\n') { ++error->line; error->col = 1; }
                else ++error->col;
            }
        }
        return false;
    }

    void ws() {
        while (p < x.size() && (x[p] == ' ' || x[p] == '\t' || x[p] == '\r' || x[p] == '\n')) ++p;
    }

    bool hex4(unsigned& value) {
        value = 0;
        for (int i = 0; i < 4; ++i) {
            if (p == x.size()) return false;
            unsigned char c = (unsigned char)x[p++];
            unsigned digit = c >= '0' && c <= '9' ? c - '0' :
                             c >= 'a' && c <= 'f' ? c - 'a' + 10 :
                             c >= 'A' && c <= 'F' ? c - 'A' + 10 : 99;
            if (digit > 15) return false;
            value = (value << 4) | digit;
        }
        return true;
    }

    static void codepoint(unsigned value, std::string& out) {
        if (value <= 0x7f) out += (char)value;
        else if (value <= 0x7ff) {
            out += (char)(0xc0 | (value >> 6));
            out += (char)(0x80 | (value & 63));
        } else if (value <= 0xffff) {
            out += (char)(0xe0 | (value >> 12));
            out += (char)(0x80 | ((value >> 6) & 63));
            out += (char)(0x80 | (value & 63));
        } else {
            out += (char)(0xf0 | (value >> 18));
            out += (char)(0x80 | ((value >> 12) & 63));
            out += (char)(0x80 | ((value >> 6) & 63));
            out += (char)(0x80 | (value & 63));
        }
    }

    bool string(std::string& out) {
        if (p == x.size() || x[p++] != '"') return bad("expected string");
        while (p < x.size()) {
            unsigned char c = (unsigned char)x[p++];
            if (c == '"') return true;
            if (c < 0x20) return bad("control character in string");
            if (c == '\\') {
                if (p == x.size()) return bad("incomplete string escape");
                char escape = x[p++];
                if (escape == '"' || escape == '\\' || escape == '/') out += escape;
                else if (escape == 'b') out += '\b';
                else if (escape == 'f') out += '\f';
                else if (escape == 'n') out += '\n';
                else if (escape == 'r') out += '\r';
                else if (escape == 't') out += '\t';
                else if (escape == 'u') {
                    unsigned value;
                    if (!hex4(value)) return bad("invalid Unicode escape");
                    if (value >= 0xd800 && value <= 0xdbff) {
                        if (p + 1 >= x.size() || x[p] != '\\' || x[p + 1] != 'u')
                            return bad("unpaired Unicode surrogate");
                        p += 2;
                        unsigned low;
                        if (!hex4(low) || low < 0xdc00 || low > 0xdfff)
                            return bad("invalid Unicode surrogate pair");
                        value = 0x10000 + ((value - 0xd800) << 10) + low - 0xdc00;
                    } else if (value >= 0xdc00 && value <= 0xdfff) {
                        return bad("unpaired Unicode surrogate");
                    }
                    codepoint(value, out);
                } else return bad("invalid string escape");
            } else if (c < 0x80) out += (char)c;
            else {
                int count = c >= 0xc2 && c <= 0xdf ? 1 :
                            c >= 0xe0 && c <= 0xef ? 2 :
                            c >= 0xf0 && c <= 0xf4 ? 3 : 0;
                if (!count || x.size() - p < (size_t)count) return bad("invalid UTF-8; save as UTF-8");
                unsigned char first = (unsigned char)x[p];
                if ((c == 0xe0 && first < 0xa0) || (c == 0xed && first >= 0xa0) ||
                    (c == 0xf0 && first < 0x90) || (c == 0xf4 && first >= 0x90))
                    return bad("invalid UTF-8 codepoint; save as UTF-8");
                out += (char)c;
                for (int i = 0; i < count; ++i) {
                    unsigned char continuation = (unsigned char)x[p++];
                    if ((continuation & 0xc0) != 0x80) return bad("invalid UTF-8 continuation; save as UTF-8");
                    out += (char)continuation;
                }
            }
            if (out.size() > MAX_STRING) return bad("string exceeds 64 KiB", 413);
        }
        return bad("unterminated string");
    }

    bool number(Value& value) {
        size_t start = p;
        if (x[p] == '-') ++p;
        if (p == x.size() || x[p] < '0' || x[p] > '9') return bad("invalid JSON value");
        if (x[p] == '0') ++p;
        else while (p < x.size() && x[p] >= '0' && x[p] <= '9') ++p;
        if (p < x.size() && x[p] == '.') {
            ++p;
            if (p == x.size() || x[p] < '0' || x[p] > '9') return bad("invalid number fraction");
            while (p < x.size() && x[p] >= '0' && x[p] <= '9') ++p;
        }
        if (p < x.size() && (x[p] == 'e' || x[p] == 'E')) {
            ++p;
            if (p < x.size() && (x[p] == '+' || x[p] == '-')) ++p;
            if (p == x.size() || x[p] < '0' || x[p] > '9') return bad("invalid number exponent");
            while (p < x.size() && x[p] >= '0' && x[p] <= '9') ++p;
        }
        value.type = Value::NUMBER;
        value.raw = x.substr(start, p - start);
        return true;
    }

    bool read(Value& value) {
        ws();
        value.begin = p;
        if (++nodes > MAX_NODES) return bad("JSON exceeds 2000000 values", 413);
        if (++depth > 64) return bad("JSON depth exceeds 64");
        if (p == x.size()) return bad("unexpected end of JSON");
        if (x[p] == '"') {
            value.type = Value::STRING;
            if (!string(value.s)) return false;
        } else if (x[p] == '{') {
            value.type = Value::OBJECT;
            ++p;
            ws();
            if (p < x.size() && x[p] == '}') ++p;
            else for (;;) {
                ws();
                std::string key;
                if (!string(key)) return false;
                ws();
                if (p == x.size() || x[p++] != ':') return bad("expected object colon");
                Value child;
                if (!read(child)) return false;
                bool duplicate = false;
                for (auto& member : value.obj) {
                    if (member.first == key) {
                        member.second = std::move(child);
                        duplicate = true;
                        break;
                    }
                }
                if (!duplicate) {
                    if (value.obj.size() >= 4096) return bad("object exceeds 4096 fields", 413);
                    value.obj.emplace_back(std::move(key), std::move(child));
                }
                ws();
                if (p < x.size() && x[p] == '}') { ++p; break; }
                if (p == x.size() || x[p++] != ',') return bad("expected object comma");
            }
        } else if (x[p] == '[') {
            value.type = Value::ARRAY;
            ++p;
            ws();
            if (p < x.size() && x[p] == ']') ++p;
            else for (;;) {
                Value child;
                if (!read(child)) return false;
                value.arr.push_back(std::move(child));
                ws();
                if (p < x.size() && x[p] == ']') { ++p; break; }
                if (p == x.size() || x[p++] != ',') return bad("expected array comma");
            }
        } else if (x.compare(p, 4, "true") == 0) {
            value.type = Value::BOOL;
            value.b = true;
            p += 4;
        } else if (x.compare(p, 5, "false") == 0) {
            value.type = Value::BOOL;
            p += 5;
        } else if (x.compare(p, 4, "null") == 0) {
            value.type = Value::NUL;
            p += 4;
        } else if (!number(value)) return false;
        value.end = p;
        --depth;
        return true;
    }

public:
    Parser(const std::string& text, Error* result) : x(text), error(result) {}

    bool parse(Value& value) {
        if (x.size() >= 3 && (unsigned char)x[0] == 0xef &&
            (unsigned char)x[1] == 0xbb && (unsigned char)x[2] == 0xbf) p = 3;
        if (x.size() >= 2 && (((unsigned char)x[0] == 0xff && (unsigned char)x[1] == 0xfe) ||
                             ((unsigned char)x[0] == 0xfe && (unsigned char)x[1] == 0xff)))
            return bad("UTF-16 is unsupported; save as UTF-8");
        if (!read(value)) return false;
        ws();
        return p == x.size() || bad("trailing JSON data");
    }
};

}
