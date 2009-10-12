from flexget import validator

class TestValidator(object):

    def test_default(self):
        root = validator.factory()
        assert root.name=='root', 'expected root'
        dv = root.accept('dict')
        assert dv.name=='dict', 'expected dict'
        dv.accept('text', key='text')
        
    def test_dict(self):
        dv = validator.factory('dict')
        dv.accept('dict', key='foo')
        result = dv.validate( {'foo': {}} )
        assert not dv.errors.messages, 'should have passed foo'
        assert result, 'invalid result for foo'
        result = dv.validate( {'bar': {}} )
        assert dv.errors.messages, 'should not have passed bar'
        assert not result, 'should have an invalid result for bar'
